"""Retention / garbage collection (SCALING_REPORT #1 e #4).

Due pulizie periodiche, eseguite dal worker:
- pruning della tabella `jobs`: la coda è anche lo storico e non veniva mai
  potata; i job terminati (done/error/canceled) oltre la retention vengono
  eliminati (l'indice ix_jobs_status_created_at supporta il filtro per status);
- GC degli export: gli export sono RIGENERABILI (originale + stato a DB),
  quindi oltre la retention si cancellano da disco e il video torna 'ready'.
  Gli ORIGINALI non vengono MAI toccati (non rigenerabili).

Le funzioni `prune_finished_jobs` / `gc_old_exports` sono testabili con una
sessione qualsiasi; `run_retention_sweep` è l'entry-point difensivo usato dal
worker (non solleva mai, protetto da esecuzioni concorrenti).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import case, delete, select, update

from ..models import Job, JobStatus, Video, VideoStatus

log = logging.getLogger("retention")

# stati terminali: solo questi sono candidati al pruning (mai queued/running)
_FINISHED = (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELED)

# protezione dalle passate concorrenti (worker_concurrency > 1): il lock è
# di modulo, acquisito non-bloccante — se un altro thread sta già pulendo,
# lo sweep corrente ritorna subito senza fare nulla.
_sweep_lock = threading.Lock()


def prune_finished_jobs(db, older_than_days: int) -> int:
    """Elimina i job terminati (done/error/canceled) con `finished_at` più
    vecchio del cutoff. Ritorna il numero di righe eliminate.

    `older_than_days` <= 0 = retention disattivata: no-op che ritorna 0.
    I job attivi (queued/running/canceling) e quelli senza `finished_at`
    non vengono mai toccati.
    """
    if older_than_days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    res = db.execute(
        delete(Job)
        .where(Job.status.in_(_FINISHED),
               Job.finished_at.is_not(None),
               Job.finished_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return int(res.rowcount or 0)


def gc_old_exports(db, exports_dir: Path, older_than_days: int) -> dict:
    """Cancella gli export più vecchi del cutoff (mtime del file su disco).

    Per i Video con `exported_path` valorizzato il cui file è scaduto:
    - `exported_path` azzerato con UPDATE condizionato committato per-video;
    - se status == 'exported' il video torna 'ready' (l'export è rigenerabile);
    - unlink del file (missing_ok) solo DOPO il commit.
    I video "occupati" (status BUSY: un job li sta lavorando) vengono saltati:
    l'export scrive sempre sullo stesso path deterministico e toccarlo durante
    un re-export cancellerebbe il file in scrittura o ne azzererebbe il
    riferimento appena committato. L'ordine DB-poi-file è deliberato: un crash
    tra commit e unlink lascia solo un file non più referenziato, raccolto come
    orfano alla passata successiva (mai un DB che punta a un file già sparito).
    Poi rimuove i file ORFANI in `exports_dir` (non referenziati da nessun
    `exported_path`) più vecchi del cutoff. Gli originali non si toccano MAI
    (questa funzione opera solo su exports_dir e sui path `exported_path`).

    `older_than_days` <= 0 = no-op. Ritorna i contatori
    {"exports_deleted": n, "orphans_deleted": m}.
    """
    counters = {"exports_deleted": 0, "orphans_deleted": 0}
    if older_than_days <= 0:
        return counters
    cutoff_ts = time.time() - older_than_days * 86400

    # 1) export referenziati scaduti: stato del video riportato indietro, poi file via.
    #    Solo le colonne necessarie: select(Video) caricherebbe l'entità intera e
    #    la relationship segments (lazy="selectin") trascinerebbe TUTTI i
    #    subtitle_segments (colonne JSON `words` incluse) a ogni sweep.
    rows = db.execute(
        select(Video.id, Video.exported_path, Video.status)
        .where(Video.exported_path.is_not(None))
    ).all()
    for vid, exported_path, status in rows:
        if status in VideoStatus.BUSY:
            continue  # un job sta lavorando il video: non interferire
        path = Path(exported_path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            # file già sparito/illeggibile: prudenza, non si tocca lo stato
            continue
        if mtime >= cutoff_ts:
            continue
        # UPDATE condizionato (niente commit di oggetti ORM stantii) committato
        # per-video PRIMA dell'unlink: se nel frattempo un job ha ripreso il
        # video (status BUSY) o il riferimento è cambiato, rowcount=0 e il file
        # non si tocca; un crash dopo il commit lascia al più un file orfano,
        # ripulito alla passata successiva.
        res = db.execute(
            update(Video)
            .where(Video.id == vid,
                   Video.exported_path == exported_path,
                   Video.status.not_in(VideoStatus.BUSY))
            .values(exported_path=None,
                    status=case((Video.status == VideoStatus.EXPORTED,
                                 VideoStatus.READY),
                                else_=Video.status))
            .execution_options(synchronize_session=False)
        )
        db.commit()
        if not res.rowcount:
            continue  # ripreso da un job concorrente: si salta, niente unlink
        path.unlink(missing_ok=True)
        counters["exports_deleted"] += 1

    # 2) file orfani in exports_dir: non referenziati da nessun exported_path
    #    (ri-lettura post-commit: i path appena azzerati non contano più)
    referenced = {
        str(Path(p).resolve())
        for p in db.execute(
            select(Video.exported_path).where(Video.exported_path.is_not(None))
        ).scalars()
    }
    try:
        entries = list(Path(exports_dir).iterdir())
    except OSError:
        entries = []
    for f in entries:
        try:
            if not f.is_file() or str(f.resolve()) in referenced:
                continue
            if f.stat().st_mtime >= cutoff_ts:
                continue
            f.unlink(missing_ok=True)
            counters["orphans_deleted"] += 1
        except OSError:
            continue  # file sparito/illeggibile nel frattempo: pazienza
    return counters


def run_retention_sweep() -> dict:
    """Una passata completa di retention. Entry-point difensivo per il worker:

    - legge la configurazione da `get_settings()` e apre le proprie sessioni;
    - ogni pulizia gira nel proprio try/except (una che fallisce non blocca
      l'altra) e la funzione non solleva MAI;
    - se un altro thread sta già pulendo (worker_concurrency > 1) ritorna
      subito con {"skipped": True}.

    Ritorna i contatori della passata (loggati a INFO se > 0).
    """
    result = {"skipped": False, "jobs_pruned": 0,
              "exports_deleted": 0, "orphans_deleted": 0}
    if not _sweep_lock.acquire(blocking=False):
        result["skipped"] = True
        return result
    try:
        # import lazy: le funzioni sopra restano usabili con sessioni arbitrarie
        # senza legare l'import del modulo all'engine di app.db
        from ..config import get_settings
        from ..db import SessionLocal

        settings = get_settings()
        try:
            with SessionLocal() as db:
                result["jobs_pruned"] = prune_finished_jobs(
                    db, settings.retention_jobs_days)
        except Exception:  # noqa: BLE001 — la retention non deve mai rompere il worker
            log.exception("Pruning dei job terminati fallito (continuo)")
        try:
            with SessionLocal() as db:
                result.update(gc_old_exports(
                    db, settings.exports_dir, settings.retention_exports_days))
        except Exception:  # noqa: BLE001
            log.exception("GC degli export fallita (continuo)")
        if result["jobs_pruned"] or result["exports_deleted"] or result["orphans_deleted"]:
            log.info("Retention: %d job eliminati, %d export scaduti, %d file orfani",
                     result["jobs_pruned"], result["exports_deleted"],
                     result["orphans_deleted"])
    except Exception:  # noqa: BLE001 — difesa estrema: mai propagare al loop
        log.exception("Sweep di retention fallito")
    finally:
        _sweep_lock.release()
    return result
