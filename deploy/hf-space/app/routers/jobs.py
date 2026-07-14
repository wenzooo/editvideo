from __future__ import annotations

import shutil
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..config import get_settings
from ..db import get_db
from ..models import Job, JobStatus, Video
from ..schemas import JobOut, StyleOut
from ..services.styles import list_styles
from ..version import APP_VERSION

router = APIRouter(prefix="/api", tags=["jobs"])


def _job_out(job: Job, name: str | None) -> JobOut:
    out = JobOut.model_validate(job)
    out.video_name = name
    return out


@router.get("/jobs", response_model=list[JobOut], dependencies=[Depends(require_auth)])
def list_jobs(active: bool = False, limit: int = Query(50, ge=1),
              db: Session = Depends(get_db)):
    q = (select(Job, Video.original_name)
         .join(Video, Video.id == Job.video_id)
         .order_by(Job.created_at.desc()).limit(min(limit, 200)))
    if active:
        q = q.where(Job.status.in_(JobStatus.ACTIVE))
    rows = db.execute(q).all()
    return [_job_out(job, name) for job, name in rows]


@router.get("/jobs/{job_id}", response_model=JobOut, dependencies=[Depends(require_auth)])
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job non trovato")
    video = db.get(Video, job.video_id)
    return _job_out(job, video.original_name if video else None)


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_auth)])
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    """Annulla un job.

    - 'queued'  -> 'canceled': dequeue logico. L'UPDATE condizionato è race-safe
      rispetto al claim del worker (se il worker lo ha appena preso, 0 righe e si
      prosegue col ramo 'running'). Il video non è ancora stato toccato, quindi
      resta nel suo stato precedente (nessun 'exporting' orfano).
    - 'running' -> 'canceling': richiesta di annullamento. Il worker non avvia il
      lavoro successivo (es. auto-export) e, a fine handler, chiude il job come
      'canceled' lasciando il video in uno stato coerente.
    - job già terminale ('done'/'error'/'canceled'): no-op idempotente.

    Risposta JSON semplice; non altera la forma delle risposte esistenti.
    """
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job non trovato")

    now = datetime.utcnow()
    res = db.execute(
        update(Job).where(Job.id == job_id, Job.status == JobStatus.QUEUED)
        .values(status=JobStatus.CANCELED, finished_at=now)
    )
    db.commit()
    if res.rowcount == 1:
        return {"ok": True, "job_id": job_id, "status": JobStatus.CANCELED, "canceled": True}

    db.execute(
        update(Job).where(Job.id == job_id, Job.status == JobStatus.RUNNING)
        .values(status=JobStatus.CANCELING)
    )
    db.commit()
    status = db.execute(select(Job.status).where(Job.id == job_id)).scalar_one()
    return {"ok": True, "job_id": job_id, "status": status,
            "canceled": status == JobStatus.CANCELED}


@router.get("/styles", response_model=list[StyleOut], dependencies=[Depends(require_auth)])
def styles():
    return list_styles()


@router.get("/health")
def health():
    s = get_settings()
    return {
        "ok": True,
        "version": APP_VERSION,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "whisper_model": s.whisper_model,
        "language": s.whisper_language or "auto",
    }
