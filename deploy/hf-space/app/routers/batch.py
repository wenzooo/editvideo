from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, noload

from ..auth import require_auth
from ..db import get_db
from ..models import Job, JobStatus, JobType, Video, VideoStatus
from ..schemas import BatchResult

router = APIRouter(prefix="/api/batch", tags=["batch"], dependencies=[Depends(require_auth)])


def _active_job_video_ids(db: Session, job_type: str) -> set[str]:
    rows = db.execute(
        select(Job.video_id).where(Job.type == job_type, Job.status.in_(JobStatus.ACTIVE))
    ).all()
    return {r[0] for r in rows}


def _enqueue_for(db: Session, statuses: list[str], job_type: str) -> BatchResult:
    # noload(segments): servono solo gli id per accodare i job; evita il selectin
    # che caricherebbe tutti i segmenti dei video coinvolti.
    videos = db.execute(
        select(Video).options(noload(Video.segments)).where(Video.status.in_(statuses))
    ).scalars().all()
    busy = _active_job_video_ids(db, job_type)
    enqueued = skipped = 0
    for v in videos:
        if v.id in busy:
            skipped += 1
            continue
        db.add(Job(video_id=v.id, type=job_type))
        enqueued += 1
    db.commit()
    return BatchResult(enqueued=enqueued, skipped=skipped)


@router.post("/transcribe", response_model=BatchResult)
def batch_transcribe(db: Session = Depends(get_db)):
    """Sottotitoli per tutti i video caricati (e per i falliti, come retry)."""
    return _enqueue_for(db, [VideoStatus.UPLOADED, VideoStatus.ERROR], JobType.TRANSCRIBE)


@router.post("/export", response_model=BatchResult)
def batch_export(db: Session = Depends(get_db)):
    """Export di tutti i video segnati come pronti."""
    return _enqueue_for(db, [VideoStatus.READY], JobType.EXPORT)


@router.post("/auto", response_model=BatchResult)
def batch_auto(db: Session = Depends(get_db)):
    """UN CLICK: fa (quasi) tutto da solo, poi si ferma per la revisione.

    Sui video caricati (o falliti, come retry): attiva taglia-silenzi e
    taglia-doppioni e li manda in trascrizione. Al termine ognuno passa a
    'da controllare' con sottotitoli + tagli GIA' applicati (anteprima pronta).
    L'export NON parte da solo (auto_export=False di proposito): prima controlli
    e modifichi, poi confermi con 'Esporta i pronti'.
    """
    videos = db.execute(
        select(Video).options(noload(Video.segments))
        .where(Video.status.in_([VideoStatus.UPLOADED, VideoStatus.ERROR]))
    ).scalars().all()
    busy = _active_job_video_ids(db, JobType.TRANSCRIBE)
    enqueued = skipped = 0
    for v in videos:
        if v.id in busy:
            skipped += 1
            continue
        v.auto_silence = True
        v.auto_retakes = True
        v.auto_export = False  # stop all'anteprima per la revisione umana
        db.add(Job(video_id=v.id, type=JobType.TRANSCRIBE))
        enqueued += 1
    db.commit()
    return BatchResult(enqueued=enqueued, skipped=skipped)


@router.post("/export-reviewed", response_model=BatchResult)
def batch_export_reviewed(db: Session = Depends(get_db)):
    """Conferma ed esporta in blocco tutti i video 'da controllare' (e i 'pronti'),
    saltando il passaggio manuale 'Segna pronto'. Comodo dopo aver dato un'occhiata
    alle anteprime prodotte da ⚡ Auto: un click e partono tutti in export."""
    return _enqueue_for(db, [VideoStatus.REVIEW, VideoStatus.READY], JobType.EXPORT)
