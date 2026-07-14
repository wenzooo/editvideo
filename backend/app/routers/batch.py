from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, noload

from ..auth import require_auth
from ..db import get_db
from ..models import Job, JobStatus, JobType, Video, VideoStatus
from ..schemas import BatchResult
from ..security import get_write_rate_limiter

# Ogni endpoint /api/batch accoda job IN MASSA (uno per video coinvolto): sono
# le rotte mutanti piu' pesanti dell'app e vanno frenate contro il flood, anche
# da un client GIA' autenticato. Il freno e' GLOBALE (una sola CHIAVE COSTANTE),
# NON per-IP: gli enqueue di massa sono operazioni globali (agiscono su TUTTO il
# parco video di un'app single-node), quindi un tetto unico per l'intero router e'
# la difesa corretta. Chiave costante = il tetto regge anche se un client autenticato
# ruota X-Forwarded-For a ogni richiesta (header spoofabile dietro il proxy HF, vedi
# client_ip): un freno per-IP sarebbe aggirabile aprendo una chiave nuova a ogni
# richiesta, questo no. require_auth resta PRIMA: un anonimo prende 401 senza
# consumare il budget del limiter (least privilege).
_BATCH_RATE_KEY = "batch"


def rate_limit_batch() -> None:
    """Freno GLOBALE agli enqueue di massa: oltre ``upload_rate_max`` richieste
    nella finestra scorrevole risponde 429 con ``Retry-After``.

    Riusa il rate limiter generico delle scritture (``get_write_rate_limiter``,
    stesse soglie ``upload_rate_*``) ma con una CHIAVE COSTANTE invece dell'IP: il
    budget e' unico per l'intero router, coerente col fatto che ogni rotta accoda
    job per tutto il parco video. Cosi' il tetto NON e' aggirabile ruotando
    ``X-Forwarded-For`` (spoofabile dietro il proxy HF), a differenza di un freno
    per-IP. Va montato DOPO ``require_auth`` (l'anonimo prende 401 senza intaccare
    il budget — least privilege).
    """
    limiter = get_write_rate_limiter()
    if limiter.is_blocked(_BATCH_RATE_KEY):
        raise HTTPException(
            status_code=429,
            detail="Troppe richieste: rallenta e riprova tra poco.",
            headers={"Retry-After": str(limiter.retry_after(_BATCH_RATE_KEY))},
        )
    limiter.record(_BATCH_RATE_KEY)


router = APIRouter(
    prefix="/api/batch",
    tags=["batch"],
    dependencies=[Depends(require_auth), Depends(rate_limit_batch)],
)


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
