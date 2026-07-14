"""Retention/GC (SCALING_REPORT #1 e #4): pruning della tabella jobs,
GC degli export scaduti e degli orfani, sweep difensivo del worker.

Ambiente isolato configurato PRIMA di importare l'app (stesso preambolo degli
altri moduli: `setdefault` con valori identici, cosi' l'ordine di import non
rompe nulla). A runtime si usano SEMPRE i percorsi di get_settings(), mai _TMP.
Ogni test ripulisce le righe DB e i file che crea (DB condiviso di suite).
Per invecchiare le righe si aggiorna `finished_at` a DB; per i file si usa
`os.utime` sull'mtime.
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_retention_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

import threading  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import delete, update  # noqa: E402

from app import worker  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Job, JobStatus, JobType, Video, VideoStatus  # noqa: E402
from app.services import retention  # noqa: E402

init_db()

OLD_DAYS = 60          # piu' vecchio di ogni retention di default (14/30 giorni)
OLD_TS = time.time() - OLD_DAYS * 86400

# righe/file creati dal test corrente, rimossi nel teardown (DB di suite condiviso)
_created_videos: list[str] = []
_created_jobs: list[str] = []
_created_files: list[Path] = []


@pytest.fixture(autouse=True)
def _cleanup():
    _created_videos.clear()
    _created_jobs.clear()
    _created_files.clear()
    yield
    with SessionLocal() as db:
        if _created_jobs:
            db.execute(delete(Job).where(Job.id.in_(_created_jobs)))
        if _created_videos:
            db.execute(delete(Job).where(Job.video_id.in_(_created_videos)))
            db.execute(delete(Video).where(Video.id.in_(_created_videos)))
        db.commit()
    for f in _created_files:
        f.unlink(missing_ok=True)


def _seed_video(**kw) -> str:
    defaults = dict(original_name="clip.mp4", stored_path="/nonexistent/clip.mp4",
                    status=VideoStatus.READY, duration=10.0, cuts=[], speedups=[])
    defaults.update(kw)
    with SessionLocal() as db:
        v = Video(**defaults)
        db.add(v)
        db.commit()
        _created_videos.append(v.id)
        return v.id


def _seed_job(video_id: str, status: str, finished_days_ago: float | None = None) -> str:
    with SessionLocal() as db:
        j = Job(video_id=video_id, type=JobType.EXPORT, status=status)
        db.add(j)
        db.commit()
        jid = j.id
        _created_jobs.append(jid)
    if finished_days_ago is not None:
        with SessionLocal() as db:  # invecchiamento della riga direttamente a DB
            db.execute(update(Job).where(Job.id == jid).values(
                finished_at=datetime.utcnow() - timedelta(days=finished_days_ago)))
            db.commit()
    return jid


def _job(jid: str) -> Job | None:
    with SessionLocal() as db:
        return db.get(Job, jid)


def _video(vid: str) -> Video:
    with SessionLocal() as db:
        return db.get(Video, vid)


def _make_file(path: Path, old: bool = False, age_days: float | None = None) -> Path:
    """Crea un file non vuoto; se `old`, con mtime piu' vecchio di ogni retention;
    con `age_days`, con mtime invecchiato esattamente di quei giorni."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-bytes")
    if old:
        os.utime(path, (OLD_TS, OLD_TS))
    elif age_days is not None:
        ts = time.time() - age_days * 86400
        os.utime(path, (ts, ts))
    _created_files.append(path)
    return path


# --------------------------------------------------------------------------- #
# prune_finished_jobs
# --------------------------------------------------------------------------- #
def test_prune_deletes_old_finished_jobs_and_keeps_the_rest():
    vid = _seed_video()
    j_done_old = _seed_job(vid, JobStatus.DONE, finished_days_ago=40)
    j_err_old = _seed_job(vid, JobStatus.ERROR, finished_days_ago=40)
    j_can_old = _seed_job(vid, JobStatus.CANCELED, finished_days_ago=40)
    j_done_recent = _seed_job(vid, JobStatus.DONE, finished_days_ago=1)
    # a cavallo del cutoff (29 < 30 GIORNI): sonda l'unita' del cutoff — con un
    # cutoff sbagliato in ore/secondi verrebbe eliminato per errore
    j_done_edge = _seed_job(vid, JobStatus.DONE, finished_days_ago=29)
    j_done_nofinish = _seed_job(vid, JobStatus.DONE)  # finished_at NULL: mai toccato
    j_queued = _seed_job(vid, JobStatus.QUEUED)
    j_running = _seed_job(vid, JobStatus.RUNNING, finished_days_ago=40)  # attivo

    with SessionLocal() as db:
        deleted = retention.prune_finished_jobs(db, older_than_days=30)

    assert deleted == 3
    assert _job(j_done_old) is None
    assert _job(j_err_old) is None
    assert _job(j_can_old) is None
    # recenti, appena dentro la finestra, attivi o senza finished_at: preservati
    for jid in (j_done_recent, j_done_edge, j_done_nofinish, j_queued, j_running):
        assert _job(jid) is not None


def test_prune_zero_or_negative_days_is_noop():
    vid = _seed_video()
    jid = _seed_job(vid, JobStatus.DONE, finished_days_ago=400)
    with SessionLocal() as db:
        assert retention.prune_finished_jobs(db, older_than_days=0) == 0
        assert retention.prune_finished_jobs(db, older_than_days=-5) == 0
    assert _job(jid) is not None


# --------------------------------------------------------------------------- #
# gc_old_exports
# --------------------------------------------------------------------------- #
def test_gc_deletes_old_exports_resets_video_and_removes_orphans():
    settings = get_settings()
    exports = settings.exports_dir

    # originale VECCHIO: non va MAI toccato (non rigenerabile)
    original = _make_file(settings.originals_dir / "ret_original.mp4", old=True)

    # export vecchio referenziato, video 'exported' -> file via, torna 'ready'
    f_old = _make_file(exports / "ret_old.mp4", old=True)
    v_old = _seed_video(status=VideoStatus.EXPORTED, exported_path=str(f_old),
                        stored_path=str(original))
    # export vecchio referenziato ma video NON 'exported' -> path azzerato, status intatto
    f_old_err = _make_file(exports / "ret_old_err.mp4", old=True)
    v_err = _seed_video(status=VideoStatus.ERROR, exported_path=str(f_old_err))
    # export recente referenziato -> intatto
    f_recent = _make_file(exports / "ret_recent.mp4")
    v_recent = _seed_video(status=VideoStatus.EXPORTED, exported_path=str(f_recent))
    # export a cavallo del cutoff (13 < 14 GIORNI) -> intatto: sonda l'unita'
    # del cutoff — con un cutoff sbagliato in ore/secondi verrebbe cancellato
    f_edge = _make_file(exports / "ret_edge.mp4", age_days=13)
    v_edge = _seed_video(status=VideoStatus.EXPORTED, exported_path=str(f_edge))
    # orfani in exports_dir: il vecchio va rimosso, il recente resta
    orphan_old = _make_file(exports / "ret_orphan_old.mp4", old=True)
    orphan_recent = _make_file(exports / "ret_orphan_recent.mp4")

    with SessionLocal() as db:
        counters = retention.gc_old_exports(db, exports, older_than_days=14)

    assert counters == {"exports_deleted": 2, "orphans_deleted": 1}

    assert not f_old.exists()
    video_old = _video(v_old)
    assert video_old.exported_path is None
    assert video_old.status == VideoStatus.READY

    assert not f_old_err.exists()
    video_err = _video(v_err)
    assert video_err.exported_path is None
    assert video_err.status == VideoStatus.ERROR  # solo 'exported' torna 'ready'

    assert f_recent.exists()
    video_recent = _video(v_recent)
    assert video_recent.exported_path == str(f_recent)
    assert video_recent.status == VideoStatus.EXPORTED

    assert f_edge.exists()  # appena dentro la finestra: deve sopravvivere
    video_edge = _video(v_edge)
    assert video_edge.exported_path == str(f_edge)
    assert video_edge.status == VideoStatus.EXPORTED

    assert not orphan_old.exists()
    assert orphan_recent.exists()
    assert original.exists()  # l'ORIGINALE non si tocca mai


def test_gc_zero_or_negative_days_is_noop():
    settings = get_settings()
    f_old = _make_file(settings.exports_dir / "ret_noop.mp4", old=True)
    vid = _seed_video(status=VideoStatus.EXPORTED, exported_path=str(f_old))
    orphan = _make_file(settings.exports_dir / "ret_noop_orphan.mp4", old=True)

    with SessionLocal() as db:
        assert retention.gc_old_exports(db, settings.exports_dir, 0) == \
            {"exports_deleted": 0, "orphans_deleted": 0}
        assert retention.gc_old_exports(db, settings.exports_dir, -1) == \
            {"exports_deleted": 0, "orphans_deleted": 0}

    assert f_old.exists() and orphan.exists()
    video = _video(vid)
    assert video.exported_path == str(f_old)
    assert video.status == VideoStatus.EXPORTED


def test_gc_skips_busy_videos_even_with_expired_export():
    """Video BUSY (job in corso): l'export scaduto NON va toccato, perché un
    re-export concorrente scrive sullo stesso path deterministico."""
    settings = get_settings()
    f_exp = _make_file(settings.exports_dir / "ret_busy_exp.mp4", old=True)
    v_exp = _seed_video(status=VideoStatus.EXPORTING, exported_path=str(f_exp))
    f_tra = _make_file(settings.exports_dir / "ret_busy_tra.mp4", old=True)
    v_tra = _seed_video(status=VideoStatus.TRANSCRIBING, exported_path=str(f_tra))

    with SessionLocal() as db:
        counters = retention.gc_old_exports(db, settings.exports_dir, 14)

    assert counters["exports_deleted"] == 0
    # i file restano (referenziati: nemmeno la passata orfani li tocca)
    assert f_exp.exists() and f_tra.exists()
    for vid, path, status in ((v_exp, f_exp, VideoStatus.EXPORTING),
                              (v_tra, f_tra, VideoStatus.TRANSCRIBING)):
        video = _video(vid)
        assert video.exported_path == str(path)
        assert video.status == status


def test_gc_missing_export_file_leaves_row_untouched():
    """exported_path che punta a un file gia' sparito: prudenza, riga intatta."""
    vid = _seed_video(status=VideoStatus.EXPORTED,
                      exported_path="/nonexistent/ret_ghost.mp4")
    with SessionLocal() as db:
        counters = retention.gc_old_exports(db, get_settings().exports_dir, 14)
    assert counters["exports_deleted"] == 0
    video = _video(vid)
    assert video.exported_path == "/nonexistent/ret_ghost.mp4"
    assert video.status == VideoStatus.EXPORTED


# --------------------------------------------------------------------------- #
# run_retention_sweep (entry-point difensivo)
# --------------------------------------------------------------------------- #
def test_run_retention_sweep_prunes_jobs_and_exports_with_default_settings():
    settings = get_settings()
    vid = _seed_video()
    j_old = _seed_job(vid, JobStatus.DONE, finished_days_ago=OLD_DAYS)
    j_recent = _seed_job(vid, JobStatus.DONE, finished_days_ago=1)
    f_old = _make_file(settings.exports_dir / "ret_sweep.mp4", old=True)
    v_exp = _seed_video(status=VideoStatus.EXPORTED, exported_path=str(f_old))

    result = retention.run_retention_sweep()

    assert result["skipped"] is False
    assert result["jobs_pruned"] >= 1
    assert result["exports_deleted"] >= 1
    assert _job(j_old) is None
    assert _job(j_recent) is not None
    assert not f_old.exists()
    video = _video(v_exp)
    assert video.exported_path is None
    assert video.status == VideoStatus.READY


def test_run_retention_sweep_skips_if_already_running():
    vid = _seed_video()
    j_old = _seed_job(vid, JobStatus.DONE, finished_days_ago=OLD_DAYS)

    assert retention._sweep_lock.acquire(blocking=False)  # simula sweep in corso
    try:
        result = retention.run_retention_sweep()
    finally:
        retention._sweep_lock.release()

    assert result["skipped"] is True
    assert result["jobs_pruned"] == 0
    assert _job(j_old) is not None  # nessuna pulizia eseguita


def test_run_retention_sweep_concurrent_calls_do_not_explode():
    errors: list[BaseException] = []
    results: list[dict] = []
    barrier = threading.Barrier(4)

    def call():
        try:
            barrier.wait(timeout=5)
            results.append(retention.run_retention_sweep())
        except BaseException as e:  # noqa: BLE001 — il test raccoglie tutto
            errors.append(e)

    threads = [threading.Thread(target=call) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []
    assert len(results) == 4 and all(isinstance(r, dict) for r in results)


def test_run_retention_sweep_never_raises_on_db_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db esploso")
    monkeypatch.setattr(retention, "prune_finished_jobs", boom)
    monkeypatch.setattr(retention, "gc_old_exports", boom)
    result = retention.run_retention_sweep()  # non deve sollevare
    assert result["jobs_pruned"] == 0 and result["exports_deleted"] == 0


# --------------------------------------------------------------------------- #
# integrazione worker_loop
# --------------------------------------------------------------------------- #
def test_worker_loop_runs_sweep_once_after_recovery(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(worker, "run_retention_sweep",
                        lambda: calls.__setitem__("n", calls["n"] + 1))
    stop = threading.Event()
    stop.set()  # il loop esegue solo recovery + sweep iniziale ed esce
    worker.worker_loop(stop_event=stop)
    assert calls["n"] == 1


def test_worker_loop_periodic_sweep_in_idle_branch(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(worker, "run_retention_sweep",
                        lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(worker, "claim_next_job", lambda: None)  # coda sempre vuota
    fake_settings = types.SimpleNamespace(worker_poll_seconds=0.01,
                                          retention_sweep_seconds=0.05)
    monkeypatch.setattr(worker, "get_settings", lambda: fake_settings)

    stop = threading.Event()
    t = threading.Thread(target=worker.worker_loop, args=(stop,), daemon=True)
    t.start()
    time.sleep(0.5)
    stop.set()
    t.join(timeout=5)
    assert not t.is_alive()
    assert calls["n"] >= 2  # sweep iniziale + almeno una passata periodica


def test_worker_loop_periodic_sweep_in_busy_branch(monkeypatch):
    """Coda SEMPRE piena (claim_next_job trova un job a ogni giro): la retention
    periodica deve girare comunque, non solo nel ramo idle — altrimenti sotto
    carico sostenuto gli export scaduti si accumulano proprio quando serve pulire."""
    calls = {"n": 0}
    monkeypatch.setattr(worker, "run_retention_sweep",
                        lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(worker, "claim_next_job", lambda: "job-finto")
    monkeypatch.setattr(worker, "run_job",
                        lambda jid: time.sleep(0.01))  # simula un job in lavorazione
    fake_settings = types.SimpleNamespace(worker_poll_seconds=0.01,
                                          retention_sweep_seconds=0.05)
    monkeypatch.setattr(worker, "get_settings", lambda: fake_settings)

    stop = threading.Event()
    t = threading.Thread(target=worker.worker_loop, args=(stop,), daemon=True)
    t.start()
    time.sleep(0.5)
    stop.set()
    t.join(timeout=5)
    assert not t.is_alive()
    assert calls["n"] >= 2  # sweep iniziale + almeno una passata a coda piena
