from __future__ import annotations

import shutil
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from ..auth import _extract_token, require_auth, verify_token
from ..config import get_settings
from ..db import get_db
from ..models import Job, JobStatus, Video
from ..schemas import JobOut, StyleOut
from ..services.metrics import collect_metrics
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


def _is_authenticated(request: Request) -> bool:
    """Variante NON-sollevante di require_auth: stessa logica di estrazione e
    verifica del token (header/cookie/query), ma ritorna un booleano invece di
    alzare 401. Serve agli endpoint a risposta "graduata" come /api/health."""
    settings = get_settings()
    return verify_token(settings.resolved_secret(), _extract_token(request))


def _db_ping(db: Session) -> bool:
    """Connettivita' DB: una SELECT 1. Non solleva mai — ritorna False se il
    database non risponde, cosi' l'health resta sempre servibile."""
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _free_mb(path) -> int | None:
    """Spazio libero (MB) sul filesystem che contiene ``path``. ``None`` se non
    determinabile (percorso ancora inesistente sul disco effimero di HF, ecc.)."""
    try:
        return shutil.disk_usage(path).free // (1024 * 1024)
    except OSError:
        return None


def _deep_health(db: Session, s) -> dict:
    """Payload diagnostico "profondo" per l'health autenticato (monitoring):
    binari ffmpeg/ffprobe, connettivita' DB, spazio disco su MEDIA_ROOT/DATA_DIR
    e heartbeat della coda (job queued/running). ``ok`` riassume lo stato
    critico (DB raggiungibile + binari presenti) per un check di liveness utile."""
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    ffprobe_ok = shutil.which("ffprobe") is not None
    db_ok = _db_ping(db)
    try:
        m = collect_metrics(db)
        queue = {"queued": m["jobs"]["queued"], "running": m["jobs"]["running"]}
    except Exception:
        queue = {"queued": None, "running": None}
    return {
        "ok": db_ok and ffmpeg_ok and ffprobe_ok,
        "version": APP_VERSION,
        "ffmpeg": ffmpeg_ok,
        "ffprobe": ffprobe_ok,
        "whisper_model": s.whisper_model,
        "language": s.whisper_language or "auto",
        "db": db_ok,
        "disk": {
            "media_root_free_mb": _free_mb(s.media_root),
            "data_dir_free_mb": _free_mb(s.data_dir),
        },
        "queue": queue,
    }


@router.get("/health")
def health(request: Request, db: Session = Depends(get_db)):
    """Health-check a due livelli (SECURITY_REPORT #8).

    - Non autenticato: solo {"ok": true} — resta usabile come uptime-check ma
      non espone versione/modello/lingua (fingerprinting).
    - Autenticato (token valido): payload diagnostico. Con ``health_deep`` (ON di
      default) aggiunge i controlli profondi delle dipendenze (DB/disco/coda);
      spegnibile per alleggerire probe molto frequenti, tornando al payload
      leggero (versione + binari + modello).
    """
    if not _is_authenticated(request):
        return {"ok": True}
    s = get_settings()
    if s.health_deep:
        return _deep_health(db, s)
    return {
        "ok": True,
        "version": APP_VERSION,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffprobe": shutil.which("ffprobe") is not None,
        "whisper_model": s.whisper_model,
        "language": s.whisper_language or "auto",
    }


@router.get("/metrics", dependencies=[Depends(require_auth)])
def metrics(db: Session = Depends(get_db)):
    """Metriche di base per il monitoring (autenticato).

    Contatori aggregati (job/video per stato, durata media dei job completati)
    calcolati con query SQLAlchemy: KISS, nessuna dipendenza Prometheus.
    Disattivabile via ``metrics_enabled`` (404 quando spento). Senza auth ->
    401 (dependency require_auth), che ha la precedenza sul flag."""
    s = get_settings()
    if not s.metrics_enabled:
        raise HTTPException(404, "Metriche non abilitate")
    return collect_metrics(db)
