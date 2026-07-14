from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, noload

from ..auth import require_auth
from ..db import get_db
from ..models import Job, JobType, Template, Video, VideoStatus
from ..schemas import ApplyTemplateIn, TemplateIn, TemplateOut, VideoOut, video_to_out
from ..services.formats import apply_template
from ..services.styles import STYLES

router = APIRouter(prefix="/api", tags=["templates"], dependencies=[Depends(require_auth)])


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(db: Session = Depends(get_db)):
    rows = db.execute(select(Template).order_by(Template.name)).scalars().all()
    return [TemplateOut.model_validate(t) for t in rows]


@router.post("/templates", response_model=TemplateOut)
def upsert_template(body: TemplateIn, db: Session = Depends(get_db)):
    """Crea o aggiorna (per nome) un Format."""
    if body.subtitle_style not in STYLES:
        raise HTTPException(422, "Stile sconosciuto")
    tpl = db.execute(select(Template).where(Template.name == body.name)).scalar_one_or_none()
    if not tpl:
        tpl = Template(name=body.name)
        db.add(tpl)
    tpl.trim_start = round(body.trim_start, 3)
    tpl.tail_trim = round(body.tail_trim, 3)
    tpl.cuts = [{"start": round(c.start, 3), "end": round(c.end, 3)} for c in body.cuts]
    tpl.subtitle_style = body.subtitle_style
    tpl.karaoke_color = body.karaoke_color
    tpl.sub_pos = body.sub_pos
    tpl.sub_scale = body.sub_scale
    tpl.auto_transcribe = body.auto_transcribe
    tpl.intro_zoom = body.intro_zoom
    tpl.auto_silence = body.auto_silence
    tpl.auto_retakes = body.auto_retakes
    tpl.auto_speedup = body.auto_speedup
    tpl.auto_export = body.auto_export
    db.commit()
    db.refresh(tpl)
    return TemplateOut.model_validate(tpl)


@router.delete("/templates/{template_id}")
def delete_template(template_id: str, db: Session = Depends(get_db)):
    tpl = db.get(Template, template_id)
    if not tpl:
        raise HTTPException(404, "Format non trovato")
    db.delete(tpl)
    db.commit()
    return {"ok": True}


@router.post("/videos/{video_id}/apply-template", response_model=VideoOut)
def apply_to_video(video_id: str, body: ApplyTemplateIn, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video:
        raise HTTPException(404, "Video non trovato")
    if video.status in VideoStatus.BUSY:
        raise HTTPException(409, "Video in lavorazione")
    tpl = db.get(Template, body.template_id)
    if not tpl:
        raise HTTPException(404, "Format non trovato")
    if not apply_template(video, tpl):
        raise HTTPException(422, "Format non applicabile a questo video (durata incompatibile)")
    db.commit()
    db.refresh(video)
    return video_to_out(video)


@router.post("/batch/apply-template")
def apply_to_uploaded(body: ApplyTemplateIn, db: Session = Depends(get_db)):
    """Applica il Format a tutti i video in stato 'caricato' (+ retry su 'errore').
    Se il Format ha auto_transcribe, accoda anche la trascrizione."""
    tpl = db.get(Template, body.template_id)
    if not tpl:
        raise HTTPException(404, "Format non trovato")
    # noload(segments): apply_template non tocca i segmenti; evita il selectin
    # che caricherebbe tutti i segmenti dei video da processare.
    videos = db.execute(
        select(Video).options(noload(Video.segments))
        .where(Video.status.in_([VideoStatus.UPLOADED, VideoStatus.ERROR]))
    ).scalars().all()
    applied = skipped = 0
    for v in videos:
        if apply_template(v, tpl):
            applied += 1
            if tpl.auto_transcribe:
                db.add(Job(video_id=v.id, type=JobType.TRANSCRIBE))
        else:
            skipped += 1
    db.commit()
    return {"applied": applied, "skipped": skipped}
