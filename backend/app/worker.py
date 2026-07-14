"""Worker della coda di lavorazione.

La coda è la tabella `jobs`: claim ottimistico (UPDATE ... WHERE status='queued'),
portabile su SQLite e Postgres, sicuro anche con più worker. Il worker gira:
- embedded (thread) in sviluppo / immagine singola;
- come processo dedicato in docker-compose:  python -m app.worker
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update

from .config import get_settings
from .db import SessionLocal, engine, init_db
from .logging_conf import log_context, setup_logging
from .models import Job, JobStatus, JobType, Video, VideoStatus
from .services import ffmpeg as ff
from .services.resilience import CircuitBreaker, retry_call
from .services.retention import run_retention_sweep
from .services.styles import build_ass
from .services.timeline import (
    apply_speedups, keep_intervals, keeps_to_plan, remap_segments_detailed_plan,
)

log = logging.getLogger("worker")

# Circuit breaker del modello Whisper PRINCIPALE: dopo alcuni job consecutivi in
# cui il modello principale fallisce (download corrotto sul disco effimero di HF,
# OOM ricorrente), il circuito si apre e le trascrizioni successive degradano
# SUBITO al modello di fallback senza sprecare tempo a riprovare quello rotto.
# Soglia/cooldown sono costanti (KISS): non meritano un env dedicato.
_WHISPER_BREAKER_THRESHOLD = 3
_WHISPER_BREAKER_COOLDOWN = 300.0
_whisper_breaker = CircuitBreaker(_WHISPER_BREAKER_THRESHOLD, _WHISPER_BREAKER_COOLDOWN)


def _transcribe_with_resilience(job_id: str, path: str, duration: float):
    """Trascrizione whisper resiliente.

    1) Se il circuit breaker del modello principale e' aperto, si salta subito
       il principale e si va di fallback (degradazione immediata).
    2) Altrimenti si prova il modello principale con retry+backoff esponenziale
       (job_max_retries / job_retry_backoff_seconds); un successo/fallimento
       aggiorna il breaker.
    3) Al fallimento persistente del principale si prova UNA SOLA VOLTA il
       modello di fallback (whisper_fallback_model). Se anche questo manca o
       fallisce, l'eccezione risale (il chiamante decide se degradare o fallire).

    Ritorna la coppia (parole, fallback_senza_parole) di ``transcribe_words``.
    """
    from .services import transcribe as tr  # lazy: import pesante
    s = get_settings()
    progress_cb = lambda p: _set_progress(job_id, p)  # noqa: E731

    if _whisper_breaker.allow():
        try:
            words = retry_call(
                lambda: tr.transcribe_words(path, duration, progress_cb=progress_cb),
                attempts=1 + max(0, s.job_max_retries),
                backoff=s.job_retry_backoff_seconds,
            )
            _whisper_breaker.record_success()
            return words
        except Exception:  # noqa: BLE001 — si tenta il fallback qui sotto
            _whisper_breaker.record_failure()
            log.warning("Modello Whisper principale fallito dopo i retry")
    else:
        log.warning("Circuit breaker Whisper aperto: salto il modello principale")

    fallback = s.whisper_fallback_model
    if not fallback:
        raise RuntimeError(
            "Trascrizione fallita col modello principale e nessun modello di fallback configurato")
    log.warning("Degrado al modello Whisper di fallback '%s' (un solo tentativo)", fallback)
    return tr.transcribe_words(path, duration, progress_cb=progress_cb, model_name=fallback)


# ------------------------------------------------------------------ claim

def claim_next_job() -> str | None:
    """Prende il job più vecchio in coda. Claim ottimistico: l'UPDATE
    condizionato garantisce che un solo worker se lo aggiudichi."""
    with SessionLocal() as db:
        job_id = db.execute(
            select(Job.id).where(Job.status == JobStatus.QUEUED)
            .order_by(Job.created_at).limit(1)
        ).scalar_one_or_none()
        if not job_id:
            return None
        res = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.QUEUED)
            .values(status=JobStatus.RUNNING, started_at=datetime.utcnow())
        )
        db.commit()
        return job_id if res.rowcount == 1 else None


# throttle degli UPDATE di progresso: ultimo istante di scrittura per job
_progress_last: dict[str, float] = {}


def _set_progress(job_id: str, value: float, min_interval: float = 0.7) -> None:
    now = time.monotonic()
    if now - _progress_last.get(job_id, 0) < min_interval:
        return
    _progress_last[job_id] = now
    with engine.begin() as conn:
        conn.execute(update(Job).where(Job.id == job_id).values(progress=round(value, 3)))


def _clear_progress(job_id: str) -> None:
    """Evita che la cache di throttling cresca indefinitamente job dopo job."""
    _progress_last.pop(job_id, None)


def _cancel_requested(db, job_id: str) -> bool:
    """True se è stata richiesta la cancellazione del job mentre era in esecuzione
    (stato 'canceling'). Usato ai checkpoint sicuri per non avviare il lavoro
    successivo e per chiudere il job come 'canceled'."""
    return db.execute(
        select(Job.status).where(Job.id == job_id)
    ).scalar_one_or_none() == JobStatus.CANCELING


# ------------------------------------------------------------------ handlers

def run_transcribe(job_id: str, video_id: str) -> None:
    from .services.transcribe import captions_with_words  # lazy
    from .services.timeline import normalize_cuts

    settings = get_settings()
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise RuntimeError("Video eliminato")
        video.status = VideoStatus.TRANSCRIBING
        db.commit()
        path, duration = video.stored_path, video.duration
        wants_silence = bool(video.auto_silence)
        wants_retakes = bool(video.auto_retakes)
        wants_speedup = bool(video.auto_speedup)
        all_cuts = list(video.cuts or [])
    speedups: list[dict] = []

    if not path or not Path(path).exists():
        raise RuntimeError(f"File sorgente mancante: {path or '(percorso vuoto)'}")

    t0 = time.monotonic()
    log.info("Trascrizione avviata (durata %.1fs, silenzi=%s, doppioni=%s)",
             duration, wants_silence, wants_retakes)

    if wants_silence or wants_speedup:  # analisi silenzi (audio-based, pre-trascrizione)
        try:
            from .services.silence import detect_silences, silences_to_cuts_and_speedups
            sil = detect_silences(path, settings.silence_noise_db, settings.silence_min_dur)
            detected, speedups = silences_to_cuts_and_speedups(
                sil, duration, leave=settings.silence_leave,
                do_cut=wants_silence, do_speedup=wants_speedup,
                speedup_min=settings.speedup_min, speedup_factor=settings.speedup_factor,
                speedup_edge=settings.speedup_edge)
            all_cuts += detected
            log.info("Silenzi su %s: %d tagli, %d velocizzati",
                     video_id[:8], len(detected), len(speedups))
        except Exception:
            log.exception("Analisi silenzi fallita su %s (continuo)", video_id[:8])

    # Trascrizione resiliente (retry + fallback modello). Se fallisce del tutto
    # e EXPORT_ALLOW_WITHOUT_SUBS e' attivo, si degrada: niente sottotitoli ma il
    # video resta montabile/esportabile (meglio un export utile che nessun export).
    subs_ok = True
    words: list = []
    fallback: list = []
    try:
        words, fallback = _transcribe_with_resilience(job_id, path, duration)
    except Exception:
        if not settings.export_allow_without_subs:
            raise  # degradazione non consentita: il job va in ERROR (fail-fast)
        subs_ok = False
        log.exception(
            "Trascrizione fallita su %s: degrado a export SENZA sottotitoli "
            "(EXPORT_ALLOW_WITHOUT_SUBS attivo)", video_id[:8])

    if subs_ok and wants_retakes:  # doppioni/ripartenze (dalle parole trascritte)
        try:
            from .services.retakes import detect_all_retake_cuts, filter_words_outside_cuts
            retake_cuts = detect_all_retake_cuts(
                words, min_match=settings.retake_min_match,
                window_s=settings.retake_window, max_cut_s=settings.retake_max_cut,
                min_match_full=settings.retake_min_match_full,
                window_full=settings.retake_window_full,
                max_cut_full=settings.retake_max_cut_full)
            if retake_cuts:
                all_cuts += retake_cuts
                words = filter_words_outside_cuts(words, retake_cuts)
                log.info("Doppioni su %s: %d tagli", video_id[:8], len(retake_cuts))
        except Exception:
            log.exception("Taglia-doppioni fallito su %s (continuo)", video_id[:8])

    captions = captions_with_words(words, fallback) if subs_ok else []
    merged_cuts = [{"start": a, "end": b} for a, b in normalize_cuts(all_cuts, duration)]

    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise RuntimeError("Video eliminato durante la trascrizione")
        from .models import SubtitleSegment
        from sqlalchemy import delete
        db.execute(delete(SubtitleSegment).where(SubtitleSegment.video_id == video_id))
        for i, c in enumerate(captions):
            db.add(SubtitleSegment(video_id=video_id, idx=i, start=c["start"],
                                   end=c["end"], text=c["text"], words=c["words"]))
        video.cuts = merged_cuts
        # tratti da velocizzare (silenzi lunghi): tenuti a parte dai tagli,
        # applicati in export come speed-up (mantengono il visivo, comprimono il tempo)
        video.speedups = speedups
        video.status = VideoStatus.REVIEW
        video.error_message = None
        # Automazione totale: upload -> sottotitoli -> export senza click.
        # Se però è stato chiesto l'annullamento mentre giravamo, NON accodiamo
        # il lavoro successivo (il video resta comunque in 'review', coerente).
        if video.auto_export and not _cancel_requested(db, job_id):
            db.add(Job(video_id=video.id, type=JobType.EXPORT))
            log.info("Auto-export accodato per %s", video_id[:8])
        elif video.auto_export:
            log.info("Auto-export saltato: job %s annullato", job_id[:8])
        db.commit()

    log.info("Trascrizione completata in %.1fs: %d caption, %d tagli",
             time.monotonic() - t0, len(captions), len(merged_cuts))


def run_export(job_id: str, video_id: str) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise RuntimeError("Video eliminato")
        video.status = VideoStatus.EXPORTING
        db.commit()

        keeps = keep_intervals(video.duration, video.trim_start, video.trim_end, video.cuts)
        # piano di render: velocizza i tratti "speedup" (silenzi lunghi) se attivo
        speedups = list(video.speedups or []) if bool(video.auto_speedup) else []
        plan = apply_speedups(keeps, speedups) if speedups else keeps_to_plan(keeps)
        segments = [
            {"start": s.start, "end": s.end, "text": s.text, "words": s.words}
            for s in video.segments
        ]
        style_id = video.subtitle_style
        karaoke_color = video.karaoke_color
        sub_pos = video.sub_pos if video.sub_pos is not None else 0.80
        sub_scale = video.sub_scale if video.sub_scale is not None else 1.0
        src, vid = video.stored_path, video.id
        intro_zoom, fps = bool(video.intro_zoom), float(video.fps or 30)
        # has_audio è già su questo oggetto: catturarlo qui evita una seconda
        # SELECT/sessione più avanti (il valore non cambia durante l'export).
        has_audio = bool(video.has_audio)

    if not src or not Path(src).exists():
        raise RuntimeError(f"File sorgente mancante: {src or '(percorso vuoto)'}")

    t0 = time.monotonic()
    log.info("Export avviato (%d segmenti, %d velocizzati, zoom=%s)",
             len(plan), sum(1 for _s, _e, spd in plan if spd > 1.0), intro_zoom)

    # difensivo: le cartelle dovrebbero esistere (ensure_dirs), ma non diamolo per scontato
    settings.subs_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)

    ass_path: Path | None = None
    remapped = remap_segments_detailed_plan(segments, plan)
    dst = settings.exports_dir / f"{vid}.mp4"
    try:
        # la scrittura dell'.ass sta dentro il try: se fallisce a metà (es. disco
        # pieno) il finally rimuove comunque il file troncato, come per l'MP4.
        if remapped:
            ass_path = settings.subs_dir / f"{vid}.ass"
            ass_path.write_text(
                build_ass(remapped, style_id, karaoke_color=karaoke_color,
                          sub_pos=sub_pos, sub_scale=sub_scale),
                encoding="utf-8")
        ff.export_video(src, dst, plan, ass_path, has_audio,
                        progress_cb=lambda p: _set_progress(job_id, p),
                        intro_zoom=intro_zoom, fps=fps)
    except Exception:
        Path(dst).unlink(missing_ok=True)  # niente output parziali sul disco
        raise
    finally:
        if ass_path:
            Path(ass_path).unlink(missing_ok=True)  # pulizia .ass temporaneo

    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if video:
            video.exported_path = str(dst)
            video.status = VideoStatus.EXPORTED
            video.error_message = None
            db.commit()

    log.info("Export completato in %.1fs", time.monotonic() - t0)


HANDLERS = {JobType.TRANSCRIBE: run_transcribe, JobType.EXPORT: run_export}


# ------------------------------------------------------------------ loop

def _persist_job_error(job_id: str, video_id: str, msg: str) -> None:
    """Registra l'errore su job.error e sullo stato del video. Best-effort: se
    anche questa scrittura fallisce, il worker non deve comunque morire né
    lasciare la coda bloccata."""
    try:
        with SessionLocal() as db:
            db.execute(update(Job).where(Job.id == job_id).values(
                status=JobStatus.ERROR, error=msg, finished_at=datetime.utcnow()))
            video = db.get(Video, video_id)
            if video:
                video.status = VideoStatus.ERROR
                video.error_message = msg
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("Impossibile registrare l'errore del job su DB")


def run_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        job_type, video_id = job.type, job.video_id

    with log_context(job_id=job_id, video_id=video_id):
        t0 = time.monotonic()
        log.info("Job %s: inizio", job_type)
        try:
            handler = HANDLERS.get(job_type)
            if not handler:
                raise RuntimeError(f"Tipo di job sconosciuto: {job_type}")
            handler(job_id, video_id)
            with SessionLocal() as db:
                if _cancel_requested(db, job_id):
                    # annullato mentre girava: lo chiudiamo come 'canceled'. Lo stato
                    # del video è già stato impostato dall'handler (coerente).
                    db.execute(update(Job).where(Job.id == job_id).values(
                        status=JobStatus.CANCELED, finished_at=datetime.utcnow()))
                    db.commit()
                    log.info("Job annullato dopo %.1fs", time.monotonic() - t0)
                else:
                    db.execute(update(Job).where(Job.id == job_id).values(
                        status=JobStatus.DONE, progress=1.0, finished_at=datetime.utcnow()))
                    db.commit()
                    log.info("Job completato in %.1fs", time.monotonic() - t0)
        except Exception as e:  # noqa: BLE001 — il worker non deve mai morire
            log.exception("Job fallito dopo %.1fs", time.monotonic() - t0)
            _persist_job_error(job_id, video_id, str(e)[:500])
        finally:
            _clear_progress(job_id)


def worker_loop(stop_event: threading.Event | None = None) -> None:
    settings = get_settings()
    stop_event = stop_event or threading.Event()
    log.info("Worker attivo (poll %.1fs)", settings.worker_poll_seconds)

    # job rimasti 'running' da un crash/riavvio -> ERRORE esplicito (mai loop di crash)
    try:
        with SessionLocal() as db:
            db.execute(update(Job).where(Job.status == JobStatus.RUNNING)
                       .values(status=JobStatus.ERROR,
                               error="Interrotto dal riavvio del server: riprova",
                               finished_at=datetime.utcnow()))
            # un annullamento in corso interrotto dal riavvio si considera concluso
            db.execute(update(Job).where(Job.status == JobStatus.CANCELING)
                       .values(status=JobStatus.CANCELED, finished_at=datetime.utcnow()))
            db.execute(update(Video).where(Video.status.in_(VideoStatus.BUSY))
                       .values(status=VideoStatus.ERROR,
                               error_message="Lavorazione interrotta dal riavvio: rilancia il job"))
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("Recovery all'avvio del worker fallita (continuo comunque)")

    # prima passata di retention subito dopo la recovery (run_retention_sweep
    # e' difensivo: non solleva mai e salta se un altro thread sta gia' pulendo)
    run_retention_sweep()
    last_sweep = time.monotonic()

    while not stop_event.is_set():
        job_id = None
        try:
            job_id = claim_next_job()
        except Exception:  # noqa: BLE001
            log.exception("Errore nel claim del job")
        if job_id:
            try:
                run_job(job_id)
            except Exception:  # noqa: BLE001 — difesa: run_job non dovrebbe mai sollevare
                log.exception("Errore imprevisto eseguendo il job %s", job_id[:8])
        else:
            stop_event.wait(settings.worker_poll_seconds)
        # retention periodica: controllata a OGNI giro, anche a coda piena
        # (proprio quando la pressione su disco e' massima). run_retention_sweep
        # e' veloce e protetto da lock, quindi non ritarda il claim successivo.
        if time.monotonic() - last_sweep >= settings.retention_sweep_seconds:
            run_retention_sweep()
            last_sweep = time.monotonic()


def start_embedded_worker() -> threading.Event:
    stop = threading.Event()
    n = max(1, get_settings().worker_concurrency)
    for i in range(n):
        t = threading.Thread(target=worker_loop, args=(stop,),
                             name=f"editvideo-worker-{i + 1}", daemon=True)
        t.start()
    return stop


if __name__ == "__main__":
    setup_logging()
    settings = get_settings()
    settings.ensure_dirs()
    log.info("Worker standalone — config effettiva: %s", settings.public_config())
    for warn in settings.validate_runtime():
        log.warning("Config sospetta: %s", warn)
    init_db()
    worker_loop()
