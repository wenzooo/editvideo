from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, noload

from ..auth import require_auth
from ..config import get_settings
from ..db import get_db
from ..models import Job, JobStatus, JobType, SubtitleSegment, Template, Video, VideoStatus
from ..schemas import JobOut, UploadResult, VideoOut, VideoPatch, video_to_out
from ..services import ffmpeg as ff
from ..services.formats import apply_template
from ..services.styles import STYLES

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/videos", tags=["videos"], dependencies=[Depends(require_auth)])

ALLOWED_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


def _get_video(db: Session, video_id: str) -> Video:
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video non trovato")
    return video


def _has_active_job(db: Session, video_id: str, job_type: str | None = None) -> bool:
    q = select(Job.id).where(Job.video_id == video_id, Job.status.in_(JobStatus.ACTIVE))
    if job_type:
        q = q.where(Job.type == job_type)
    return db.execute(q.limit(1)).first() is not None


def enqueue_job(db: Session, video: Video, job_type: str) -> Job:
    job = Job(video_id=video.id, type=job_type)
    db.add(job)
    return job


@router.post("/upload", response_model=UploadResult)
def upload(
    files: list[UploadFile] = File(...),
    template_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    # tetto sul NUMERO di parti file, verificato prima di processare qualunque
    # file: evita batch abnormi in una singola richiesta (SCALING_REPORT #3).
    # Il tetto sui BYTE della richiesta sta nel middleware in main.py, che
    # respinge su Content-Length prima ancora che il body venga letto.
    if len(files) > settings.max_upload_files:
        raise HTTPException(
            413, f"Troppi file in una sola richiesta: massimo "
                 f"{settings.max_upload_files} (ricevuti {len(files)})")
    created: list[VideoOut] = []
    errors: list[dict] = []

    template: Template | None = None
    if template_id:
        template = db.get(Template, template_id)
        if not template:
            raise HTTPException(404, "Format non trovato")

    for f in files:
        name = Path(f.filename or "video").name
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXT:
            errors.append({"name": name, "reason": f"Formato non supportato ({ext})"})
            continue

        video = Video(original_name=name, stored_path="")
        dst = settings.originals_dir / f"{video.id}{ext}"
        try:
            max_bytes = settings.max_upload_mb * 1024 * 1024
            written = 0
            with open(dst, "wb") as out:
                while True:
                    chunk = f.file.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError(f"File oltre il limite di {settings.max_upload_mb} MB")
                    out.write(chunk)
            meta = ff.probe(dst)
            video.stored_path = str(dst)
            video.duration = meta["duration"]
            video.width = meta["width"]
            video.height = meta["height"]
            video.fps = meta["fps"]
            video.has_audio = meta["has_audio"]
            video.size_bytes = dst.stat().st_size

            thumb = settings.thumbnails_dir / f"{video.id}.jpg"
            try:
                ff.make_thumbnail(dst, thumb, at=min(1.0, meta["duration"] / 2))
                video.thumbnail_path = str(thumb)
            except ff.FFmpegError as e:  # la thumbnail non blocca l'upload
                log.warning("thumbnail fallita per %s: %s", name, e)

            if template:
                apply_template(video, template)  # se non applicabile: video intonso

            db.add(video)
            db.commit()
            if template and template.auto_transcribe:
                db.add(Job(video_id=video.id, type=JobType.TRANSCRIBE))
                db.commit()
            db.refresh(video)
            created.append(video_to_out(video))
        except Exception as e:
            db.rollback()
            dst.unlink(missing_ok=True)
            errors.append({"name": name, "reason": str(e)[:300]})

    return UploadResult(created=created, errors=errors)


@router.get("", response_model=list[VideoOut])
def list_videos(status: str | None = None, db: Session = Depends(get_db)):
    # noload(segments): la relationship e' lazy="selectin", quindi un semplice
    # select(Video) trascinerebbe TUTTI i segmenti di TUTTI i video in una query
    # extra. Qui il conteggio arriva dall'aggregata sotto e video_to_out non tocca
    # v.segments: sopprimiamo il caricamento (ORDER BY created_at usa l'indice).
    q = select(Video).options(noload(Video.segments)).order_by(Video.created_at.desc())
    if status:
        q = q.where(Video.status == status)
    videos = db.execute(q).scalars().all()
    # conteggio sottotitoli in UNA query aggregata: la dashboard fa polling
    # continuo e non deve trascinarsi dietro tutti i segmenti di tutti i video
    counts = dict(db.execute(
        select(SubtitleSegment.video_id, func.count(SubtitleSegment.id))
        .group_by(SubtitleSegment.video_id)
    ).all())
    return [video_to_out(v, subtitle_count=counts.get(v.id, 0)) for v in videos]


@router.get("/{video_id}", response_model=VideoOut)
def get_video(video_id: str, db: Session = Depends(get_db)):
    return video_to_out(_get_video(db, video_id))


@router.patch("/{video_id}", response_model=VideoOut)
def patch_video(video_id: str, body: VideoPatch, db: Session = Depends(get_db)):
    video = _get_video(db, video_id)
    if video.status in VideoStatus.BUSY:
        raise HTTPException(409, "Video in lavorazione: attendi la fine del job")

    if body.trim_start is not None:
        if body.trim_start >= video.duration:
            raise HTTPException(422, "trim_start oltre la durata del video")
        new_start = round(body.trim_start, 3)
        # coerenza col trim_end: se in questa PATCH non stiamo cambiando il
        # trim_end, il trim_start non può superare quello già salvato, altrimenti
        # la finestra di trim resta vuota e l'export fallisce a valle
        # ("Intervallo di trim vuoto"). Se trim_end viene passato o azzerato,
        # ci pensa la validazione sottostante.
        keeps_saved_trim_end = not body.clear_trim_end and body.trim_end is None
        if (keeps_saved_trim_end and video.trim_end is not None
                and new_start >= video.trim_end):
            raise HTTPException(422, "trim_start oltre il trim_end impostato")
        video.trim_start = new_start
    if body.clear_trim_end:
        video.trim_end = None
    elif body.trim_end is not None:
        if body.trim_end <= video.trim_start or body.trim_end > video.duration + 0.5:
            raise HTTPException(422, "trim_end non valido")
        video.trim_end = round(min(body.trim_end, video.duration), 3)
    if body.cuts is not None:
        for c in body.cuts:
            if c.end > video.duration + 0.5:
                raise HTTPException(422, "Un taglio esce dalla durata del video")
        video.cuts = [{"start": round(c.start, 3), "end": round(min(c.end, video.duration), 3)}
                      for c in body.cuts]
    if body.subtitle_style is not None:
        if body.subtitle_style not in STYLES:
            raise HTTPException(422, "Stile sconosciuto")
        video.subtitle_style = body.subtitle_style
    if body.karaoke_color is not None:
        # già normalizzato a "#RRGGBB" dallo schema (input invalido -> 422)
        video.karaoke_color = body.karaoke_color
    if body.sub_pos is not None:
        # posizione verticale del blocco testo, clamp difensivo in [0.05, 0.95]
        video.sub_pos = round(max(0.05, min(0.95, body.sub_pos)), 3)
    if body.sub_scale is not None:
        video.sub_scale = round(max(0.5, min(2.5, body.sub_scale)), 3)
    if body.intro_zoom is not None:
        video.intro_zoom = body.intro_zoom
    if body.auto_silence is not None:
        video.auto_silence = body.auto_silence
    if body.auto_retakes is not None:
        video.auto_retakes = body.auto_retakes
    if body.auto_speedup is not None:
        video.auto_speedup = body.auto_speedup
    if body.auto_export is not None:
        video.auto_export = body.auto_export
    if body.status is not None:
        if body.status not in VideoStatus.USER_SETTABLE:
            raise HTTPException(422, f"Stato non impostabile manualmente: {body.status}")
        video.status = body.status
        video.error_message = None

    db.commit()
    db.refresh(video)
    return video_to_out(video)


@router.post("/{video_id}/autocut", response_model=VideoOut)
def autocut(video_id: str, db: Session = Depends(get_db)):
    """Rileva i silenzi e li aggiunge come tagli (fusi con quelli esistenti)."""
    from ..services.silence import auto_cuts_for
    from ..services.timeline import normalize_cuts

    video = _get_video(db, video_id)
    if video.status in VideoStatus.BUSY:
        raise HTTPException(409, "Video in lavorazione")
    s = get_settings()
    try:
        detected = auto_cuts_for(video.stored_path, video.duration,
                                 s.silence_noise_db, s.silence_min_dur, s.silence_leave)
    except Exception as e:
        raise HTTPException(500, f"Rilevamento silenzi fallito: {str(e)[:200]}")
    video.cuts = [{"start": a, "end": b}
                  for a, b in normalize_cuts(list(video.cuts or []) + detected, video.duration)]
    db.commit()
    db.refresh(video)
    return video_to_out(video)


@router.post("/{video_id}/retakes", response_model=VideoOut)
def retakes(video_id: str, db: Session = Depends(get_db)):
    """Rileva doppioni/ripartenze dai sottotitoli (ripartenze brevi + riprese
    dell'intero discorso) e li aggiunge come tagli, fusi con quelli esistenti.

    On-demand dall'editor: riusa le parole gia' trascritte (SubtitleSegment.words),
    non ritrascrive. Non azzera i tagli manuali/silenzi gia' presenti."""
    from ..services.retakes import detect_all_retake_cuts
    from ..services.timeline import normalize_cuts

    video = _get_video(db, video_id)
    if video.status in VideoStatus.BUSY:
        raise HTTPException(409, "Video in lavorazione")

    # flatten delle parole per-parola di TUTTI i segmenti, ordinati per idx
    # (la relationship Video.segments e' gia' order_by idx)
    words: list[tuple[float, float, str]] = []
    for seg in video.segments:
        for w in seg.words or []:
            try:
                words.append((float(w[0]), float(w[1]), str(w[2])))
            except (TypeError, ValueError, IndexError):
                continue  # parola malformata: la salto, non blocco l'intera azione
    if not words:
        raise HTTPException(400, "Genera prima i sottotitoli")

    s = get_settings()
    retake_cuts = detect_all_retake_cuts(
        words, min_match=s.retake_min_match, window_s=s.retake_window,
        max_cut_s=s.retake_max_cut, min_match_full=s.retake_min_match_full,
        window_full=s.retake_window_full, max_cut_full=s.retake_max_cut_full)
    video.cuts = [{"start": a, "end": b}
                  for a, b in normalize_cuts(list(video.cuts or []) + retake_cuts, video.duration)]
    db.commit()
    db.refresh(video)
    return video_to_out(video)


@router.delete("/{video_id}")
def delete_video(video_id: str, db: Session = Depends(get_db)):
    video = _get_video(db, video_id)
    if video.status in VideoStatus.BUSY or _has_active_job(db, video_id):
        raise HTTPException(409, "Video in lavorazione: impossibile eliminare ora")
    for p in (video.stored_path, video.thumbnail_path, video.exported_path):
        if p:
            Path(p).unlink(missing_ok=True)
    (get_settings().subs_dir / f"{video.id}.ass").unlink(missing_ok=True)
    db.delete(video)
    db.commit()
    return {"ok": True}


# ---- file serviti (protetti dal cookie di sessione) ----
#
# Caching (PERFORMANCE): i media qui sono privati (dietro auth) e derivano dall'id
# del video, quindi il loro contenuto per un dato path non cambia (l'originale e'
# scritto una volta all'upload; l'export e' rigenerato con un path stabile). Si
# aggiunge Cache-Control (max-age = media_cache_max_age) + un ETag DEBOLE su
# mtime+size, cosi' il browser puo' rivalidare con If-None-Match e ricevere 304
# (nessun ri-trasferimento del file). L'ETag e' debole perche' la GZip a monte
# puo' trasformare i byte: la validazione If-None-Match e' comunque debole (RFC 7232).


def _media_cache_headers(file_path: str, *, immutable: bool = False) -> dict[str, str]:
    """Header di caching per un file media: Cache-Control privato con max-age =
    media_cache_max_age (+ immutable per contenuti che non cambiano mai, es. gli
    export dal nome derivato dall'id) ed ETag debole basato su mtime+size."""
    st = Path(file_path).stat()
    max_age = get_settings().media_cache_max_age
    cache_control = f"private, max-age={max_age}"
    if immutable:
        cache_control += ", immutable"
    etag = f'W/"{st.st_size:x}-{st.st_mtime_ns:x}"'
    return {"Cache-Control": cache_control, "ETag": etag}


def _etag_matches(if_none_match: str, etag: str) -> bool:
    """True se l'header If-None-Match del client soddisfa l'ETag corrente.
    Confronto DEBOLE (RFC 7232): si ignora il prefisso W/ e si accetta '*' o una
    lista di candidati separati da virgola (il browser rimanda quello ricevuto)."""
    if if_none_match.strip() == "*":
        return True
    target = etag.removeprefix("W/").strip()
    return any(c.strip().removeprefix("W/").strip() == target
               for c in if_none_match.split(","))


def _serve_media(request: Request, file_path: str, media_type: str, *,
                 immutable: bool = False, filename: str | None = None) -> Response:
    """Serve un file media con header di caching + gestione condizionale.
    Se l'If-None-Match del client combacia con l'ETag corrente ritorna 304
    (senza corpo) con gli stessi header; altrimenti la FileResponse completa."""
    headers = _media_cache_headers(file_path, immutable=immutable)
    inm = request.headers.get("if-none-match")
    if inm and _etag_matches(inm, headers["ETag"]):
        return Response(status_code=304, headers=headers)
    return FileResponse(file_path, media_type=media_type, headers=headers,
                        filename=filename)


@router.get("/{video_id}/file")
def video_file(video_id: str, request: Request, db: Session = Depends(get_db)):
    video = _get_video(db, video_id)
    if not Path(video.stored_path).exists():
        raise HTTPException(404, "File originale mancante")
    media_type = mimetypes.guess_type(video.stored_path)[0] or "video/mp4"
    return _serve_media(request, video.stored_path, media_type)


@router.get("/{video_id}/thumbnail")
def thumbnail(video_id: str, request: Request, db: Session = Depends(get_db)):
    video = _get_video(db, video_id)
    if not video.thumbnail_path or not Path(video.thumbnail_path).exists():
        raise HTTPException(404, "Thumbnail mancante")
    return _serve_media(request, video.thumbnail_path, "image/jpeg")


@router.get("/{video_id}/export/file")
def export_file(video_id: str, request: Request, db: Session = Depends(get_db)):
    video = _get_video(db, video_id)
    if not video.exported_path or not Path(video.exported_path).exists():
        raise HTTPException(404, "Nessun export disponibile")
    # export immutabile: path derivato dall'id, contenuto stabile una volta prodotto
    return _serve_media(request, video.exported_path, "video/mp4", immutable=True)


@router.get("/{video_id}/export/download")
def export_download(video_id: str, request: Request, db: Session = Depends(get_db)):
    video = _get_video(db, video_id)
    if not video.exported_path or not Path(video.exported_path).exists():
        raise HTTPException(404, "Nessun export disponibile")
    stem = re.sub(r"[^\w\-. ]", "_", Path(video.original_name).stem) or "video"
    return _serve_media(request, video.exported_path, "video/mp4", immutable=True,
                        filename=f"{stem}_final.mp4")


# ---- azioni (creano job in coda) ----

@router.post("/{video_id}/transcribe", response_model=JobOut)
def transcribe(video_id: str, db: Session = Depends(get_db)):
    video = _get_video(db, video_id)
    if video.status in VideoStatus.BUSY:
        raise HTTPException(409, "Video già in lavorazione")
    if _has_active_job(db, video_id, JobType.TRANSCRIBE):
        raise HTTPException(409, "Trascrizione già in coda")
    job = enqueue_job(db, video, JobType.TRANSCRIBE)
    db.commit()
    db.refresh(job)
    return JobOut.model_validate(job)


@router.post("/{video_id}/export", response_model=JobOut)
def export(video_id: str, db: Session = Depends(get_db)):
    video = _get_video(db, video_id)
    if video.status in VideoStatus.BUSY:
        raise HTTPException(409, "Video già in lavorazione")
    if _has_active_job(db, video_id, JobType.EXPORT):
        raise HTTPException(409, "Export già in coda")
    job = enqueue_job(db, video, JobType.EXPORT)
    db.commit()
    db.refresh(job)
    return JobOut.model_validate(job)
