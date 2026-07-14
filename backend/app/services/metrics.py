"""Aggregazioni riusabili per l'osservabilita' (health profondo e /api/metrics).

Funzioni pure sul DB: contatori e durate calcolati con query aggregate
SQLAlchemy, senza effetti collaterali e senza dipendenze esterne (niente
Prometheus, inappropriato per un'app single-node). L'health profondo e
l'endpoint /api/metrics condividono queste funzioni per non duplicare la logica
di conteggio (DRY).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Job, JobStatus, Video


def _count_by_status(db: Session, model) -> dict[str, int]:
    """Conta le righe di ``model`` raggruppate per la colonna ``status``.

    Ritorna ``{stato: conteggio}``; gli stati senza righe semplicemente non
    compaiono nel dizionario. Primitiva condivisa da tutte le aggregazioni qui
    sotto (una sola GROUP BY invece di N COUNT separati)."""
    rows = db.execute(select(model.status, func.count()).group_by(model.status)).all()
    return {status: int(n) for status, n in rows}


def _avg_done_job_seconds(db: Session) -> float | None:
    """Durata media (s) dei job completati con successo.

    Considera solo i job ``done`` con ``started_at``/``finished_at`` valorizzati.
    La differenza tra timestamp e' calcolata in Python per restare agnostici
    rispetto al backend (SQLite e Postgres divergono nell'aritmetica sulle date);
    su scala single-node il numero di job e' contenuto, quindi caricare le coppie
    e' accettabile (KISS). Ritorna ``None`` se non c'e' ancora alcun job
    completato misurabile."""
    rows = db.execute(
        select(Job.started_at, Job.finished_at).where(
            Job.status == JobStatus.DONE,
            Job.started_at.is_not(None),
            Job.finished_at.is_not(None),
        )
    ).all()
    durate = [
        (finished - started).total_seconds()
        for started, finished in rows
        if finished >= started
    ]
    if not durate:
        return None
    return round(sum(durate) / len(durate), 3)


def collect_metrics(db: Session) -> dict:
    """Raccoglie in un unico dizionario i contatori utili al monitoring.

    - ``jobs``: conteggio per stato (+ totale, + scorciatoie queued/running);
    - ``videos``: conteggio per stato (+ totale);
    - ``avg_done_job_seconds``: durata media dei job completati (``None`` se n/d).

    Condivisa tra /api/metrics e l'health profondo: la coda (queued/running) e'
    esposta qui una volta sola per non duplicare la query."""
    jobs_by_status = _count_by_status(db, Job)
    videos_by_status = _count_by_status(db, Video)
    return {
        "jobs": {
            "by_status": jobs_by_status,
            "total": sum(jobs_by_status.values()),
            "queued": jobs_by_status.get(JobStatus.QUEUED, 0),
            "running": jobs_by_status.get(JobStatus.RUNNING, 0),
        },
        "videos": {
            "by_status": videos_by_status,
            "total": sum(videos_by_status.values()),
        },
        "avg_done_job_seconds": _avg_done_job_seconds(db),
    }
