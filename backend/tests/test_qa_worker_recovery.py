"""QA worker: recovery all'avvio, run_export (sorgente mancante / happy path),
chiusura job con HANDLERS ignoto, throttling di _set_progress, run_transcribe
con auto_export (accodato o saltato se il job e' in annullamento) e con
analisi silenzi fallita (QA-07: si prosegue senza tagli, job DONE).

Ambiente isolato configurato PRIMA di importare l'app (stesso preambolo degli
altri moduli: `setdefault` con valori identici, cosi' l'ordine di import non
rompe nulla). A runtime si usano SEMPRE i percorsi di get_settings(), mai _TMP.
Nessuna chiamata HTTP: si esercita direttamente app.worker. Ogni test ripulisce
le righe DB che crea (il DB e' condiviso con il resto della suite).
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_qa_worker_recovery_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

import threading  # noqa: E402
import types  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app import worker  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Job, JobStatus, JobType, SubtitleSegment, Video, VideoStatus,
)
from app.services import silence as silence_mod  # noqa: E402
from app.services import transcribe as transcribe_mod  # noqa: E402

init_db()

# righe create dal test corrente, rimosse nel teardown (DB condiviso di suite)
_created_videos: list[str] = []
_created_jobs: list[str] = []


@pytest.fixture(autouse=True)
def _db_cleanup():
    _created_videos.clear()
    _created_jobs.clear()
    yield
    with SessionLocal() as db:
        if _created_videos:
            db.execute(delete(Job).where(Job.video_id.in_(_created_videos)))
            db.execute(delete(SubtitleSegment)
                       .where(SubtitleSegment.video_id.in_(_created_videos)))
        if _created_jobs:
            db.execute(delete(Job).where(Job.id.in_(_created_jobs)))
        if _created_videos:
            db.execute(delete(Video).where(Video.id.in_(_created_videos)))
        db.commit()


def _seed_video(**kw) -> str:
    defaults = dict(original_name="clip.mp4", stored_path="/nonexistent/clip.mp4",
                    status=VideoStatus.READY, duration=10.0,
                    cuts=[], speedups=[], auto_speedup=False)
    defaults.update(kw)
    with SessionLocal() as db:
        v = Video(**defaults)
        db.add(v)
        db.commit()
        _created_videos.append(v.id)
        return v.id


def _seed_job(video_id: str, jtype: str = JobType.EXPORT,
              status: str = JobStatus.RUNNING) -> str:
    with SessionLocal() as db:
        j = Job(video_id=video_id, type=jtype, status=status)
        db.add(j)
        db.commit()
        _created_jobs.append(j.id)
        return j.id


def _job(jid: str) -> Job:
    with SessionLocal() as db:
        return db.get(Job, jid)


def _video(vid: str) -> Video:
    with SessionLocal() as db:
        return db.get(Video, vid)


def _fake_export(calls: dict):
    """Sostituto di ff.export_video: scrive un output fittizio non vuoto e
    registra se il file .ass esisteva al momento della chiamata."""
    def fake(src, dst, plan, ass_path, has_audio,
             progress_cb=None, intro_zoom=False, fps=30.0):
        calls["ass_path"] = ass_path
        calls["ass_existed"] = bool(ass_path) and Path(ass_path).exists()
        calls["dst"] = Path(dst)
        Path(dst).write_bytes(b"fake-mp4-not-empty")
    return fake


# --------------------------------------------------------------------------- #
# 1) recovery all'avvio del worker_loop (stop_event gia' settato: solo recovery)
# --------------------------------------------------------------------------- #
def test_worker_loop_startup_recovery_resets_stale_state():
    v_run = _seed_video()
    j_run = _seed_job(v_run, status=JobStatus.RUNNING)
    v_can = _seed_video()
    j_can = _seed_job(v_can, status=JobStatus.CANCELING)
    v_busy = _seed_video(status=VideoStatus.TRANSCRIBING)

    stop = threading.Event()
    stop.set()  # il loop esegue solo il blocco di recovery ed esce subito
    worker.worker_loop(stop_event=stop)

    job_run = _job(j_run)
    assert job_run.status == JobStatus.ERROR
    assert job_run.error and job_run.error.startswith("Interrotto dal riavvio")
    assert job_run.finished_at is not None

    job_can = _job(j_can)
    assert job_can.status == JobStatus.CANCELED
    assert job_can.finished_at is not None

    video_busy = _video(v_busy)
    assert video_busy.status == VideoStatus.ERROR
    assert video_busy.error_message and "riavvio" in video_busy.error_message


# --------------------------------------------------------------------------- #
# 2) run_export con sorgente mancante -> job ERROR + video ERROR
# --------------------------------------------------------------------------- #
def test_run_export_missing_source_marks_job_and_video_error():
    vid = _seed_video(status=VideoStatus.EXPORTING,
                      stored_path="/nonexistent/ev_qa_missing.mp4")
    jid = _seed_job(vid, jtype=JobType.EXPORT, status=JobStatus.RUNNING)

    worker.run_job(jid)  # non deve sollevare: l'errore viene persistito

    job = _job(jid)
    assert job.status == JobStatus.ERROR
    assert job.error and "File sorgente mancante" in job.error
    assert len(job.error) <= 500  # troncato a 500 char da run_job
    assert job.finished_at is not None
    video = _video(vid)
    assert video.status == VideoStatus.ERROR
    assert video.error_message and "File sorgente mancante" in video.error_message


# --------------------------------------------------------------------------- #
# 3) run_export felice (ffmpeg mockato): EXPORTED + .ass temporaneo rimosso
# --------------------------------------------------------------------------- #
def test_run_export_happy_path_exports_and_cleans_ass(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"sorgente finta")
    vid = _seed_video(stored_path=str(src), duration=10.0)
    with SessionLocal() as db:  # segmenti seminati -> il file .ass viene scritto
        db.add(SubtitleSegment(video_id=vid, idx=0, start=1.0, end=3.0,
                               text="ciao mondo",
                               words=[[1.0, 1.8, "ciao"], [2.0, 2.8, "mondo"]]))
        db.commit()
    jid = _seed_job(vid, jtype=JobType.EXPORT, status=JobStatus.RUNNING)

    calls: dict = {}
    monkeypatch.setattr(worker.ff, "export_video", _fake_export(calls))
    worker.run_job(jid)

    settings = get_settings()
    ass_path = settings.subs_dir / f"{vid}.ass"
    assert calls["ass_existed"] is True         # l'.ass c'era durante l'export
    assert calls["ass_path"] == ass_path
    assert not ass_path.exists()                # ... ed e' stato rimosso nel finally

    video = _video(vid)
    assert video.status == VideoStatus.EXPORTED
    assert video.exported_path == str(settings.exports_dir / f"{vid}.mp4")
    assert Path(video.exported_path).exists()
    job = _job(jid)
    assert job.status == JobStatus.DONE and job.progress == 1.0

    Path(video.exported_path).unlink(missing_ok=True)  # niente residui su disco


# --------------------------------------------------------------------------- #
# 4) CHARACTERIZATION QA-14: run_export NON ha checkpoint di cancellazione.
# Un job export gia' in 'canceling' quando run_job parte viene comunque
# eseguito fino in fondo: il job viene chiuso come CANCELED ma il video
# risulta EXPORTED con exported_path settato. Stato incoerente ("annullato"
# per l'utente, ma l'export e' avvenuto e il file esiste) documentato qui:
# se in futuro run_export guadagna un checkpoint, questo test va aggiornato.
# --------------------------------------------------------------------------- #
def test_qa14_canceling_export_still_completes_video_exported(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"sorgente finta")
    vid = _seed_video(stored_path=str(src), duration=10.0)
    jid = _seed_job(vid, jtype=JobType.EXPORT, status=JobStatus.CANCELING)

    calls: dict = {}
    monkeypatch.setattr(worker.ff, "export_video", _fake_export(calls))
    worker.run_job(jid)

    job = _job(jid)
    assert job.status == JobStatus.CANCELED     # chiuso come annullato...
    assert job.finished_at is not None
    video = _video(vid)
    assert video.status == VideoStatus.EXPORTED  # ...ma l'export e' stato fatto
    assert video.exported_path
    assert calls["dst"].exists()                 # e il file di output esiste

    calls["dst"].unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 5) run_job con tipo di job senza handler -> ERROR esplicito, video ERROR
# --------------------------------------------------------------------------- #
def test_run_job_unknown_handler_marks_error(monkeypatch):
    vid = _seed_video()
    jid = _seed_job(vid, jtype=JobType.EXPORT, status=JobStatus.RUNNING)
    monkeypatch.setattr(worker, "HANDLERS", {})

    worker.run_job(jid)  # non deve sollevare

    job = _job(jid)
    assert job.status == JobStatus.ERROR
    assert job.error and "Tipo di job sconosciuto" in job.error
    video = _video(vid)
    assert video.status == VideoStatus.ERROR
    assert video.error_message and "Tipo di job sconosciuto" in video.error_message


# --------------------------------------------------------------------------- #
# 6) _set_progress: throttling con clock controllato
# --------------------------------------------------------------------------- #
def test_set_progress_throttles_within_min_interval(monkeypatch):
    vid = _seed_video()
    jid = _seed_job(vid, status=JobStatus.RUNNING)

    clock = {"now": 1000.0}
    monkeypatch.setattr(worker, "time",
                        types.SimpleNamespace(monotonic=lambda: clock["now"]))

    def progress() -> float:
        with SessionLocal() as db:
            return db.execute(select(Job.progress).where(Job.id == jid)).scalar_one()

    try:
        worker._set_progress(jid, 0.2)   # prima scrittura (cache vuota): scrive
        assert progress() == 0.2
        clock["now"] = 1000.3            # entro min_interval (0.7s): NON scrive
        worker._set_progress(jid, 0.5)
        assert progress() == 0.2         # resta il primo valore
        clock["now"] = 1000.8            # oltre l'intervallo dall'ultima scrittura
        worker._set_progress(jid, 0.9)
        assert progress() == 0.9
    finally:
        worker._clear_progress(jid)      # niente residui nella cache di throttling


# --------------------------------------------------------------------------- #
# 7) run_transcribe + auto_export (transcribe_words mockato NEL MODULO SORGENTE,
#    perche' run_transcribe lo importa lazy dentro la funzione)
# --------------------------------------------------------------------------- #
def _fake_transcribe_words(path, duration, progress_cb=None):
    # poche parole finte, nessun segmento di fallback
    return ([(0.5, 1.0, " ciao"), (1.1, 1.6, " mondo.")], [])


def test_run_transcribe_auto_export_enqueues_export_job(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"sorgente finta")
    vid = _seed_video(status=VideoStatus.UPLOADED, stored_path=str(src),
                      duration=10.0, auto_export=True, auto_silence=False,
                      auto_retakes=False, auto_speedup=False)
    jid = _seed_job(vid, jtype=JobType.TRANSCRIBE, status=JobStatus.RUNNING)
    monkeypatch.setattr(transcribe_mod, "transcribe_words", _fake_transcribe_words)

    worker.run_job(jid)

    with SessionLocal() as db:
        segs = db.execute(select(SubtitleSegment)
                          .where(SubtitleSegment.video_id == vid)).scalars().all()
    assert len(segs) >= 1 and segs[0].text == "ciao mondo."
    video = _video(vid)
    assert video.status == VideoStatus.REVIEW
    assert _job(jid).status == JobStatus.DONE
    with SessionLocal() as db:  # auto-export accodato: nuovo job EXPORT 'queued'
        exports = db.execute(select(Job).where(
            Job.video_id == vid, Job.type == JobType.EXPORT)).scalars().all()
    assert len(exports) == 1
    assert exports[0].status == JobStatus.QUEUED
    assert exports[0].id != jid


def test_run_transcribe_silence_failure_continues_without_cuts(tmp_path, monkeypatch):
    """QA-07: detect_silences ora SOLLEVA su ffmpeg fallito (mai [] silenzioso).
    Il contratto promesso in silence.py e' che run_transcribe logga e prosegue
    senza tagli: il job deve chiudersi DONE e il video andare in 'review', mai
    fallire l'intera trascrizione per un'analisi silenzi andata male."""
    src = tmp_path / "src.mp4"
    src.write_bytes(b"sorgente finta")
    vid = _seed_video(status=VideoStatus.UPLOADED, stored_path=str(src),
                      duration=10.0, auto_export=False, auto_silence=True,
                      auto_retakes=False, auto_speedup=False)
    jid = _seed_job(vid, jtype=JobType.TRANSCRIBE, status=JobStatus.RUNNING)
    monkeypatch.setattr(transcribe_mod, "transcribe_words", _fake_transcribe_words)

    def boom(*a, **kw):  # run_transcribe importa lazy dal modulo sorgente
        raise RuntimeError("ffmpeg silencedetect fallito (returncode=1): audio rotto")
    monkeypatch.setattr(silence_mod, "detect_silences", boom)

    worker.run_job(jid)

    job = _job(jid)
    assert job.status == JobStatus.DONE  # il job NON va in ERROR
    assert job.error is None
    video = _video(vid)
    assert video.status == VideoStatus.REVIEW
    assert video.error_message is None
    assert video.cuts == []      # analisi silenzi saltata: nessun taglio
    assert video.speedups == []
    with SessionLocal() as db:   # la trascrizione e' comunque arrivata in fondo
        segs = db.execute(select(SubtitleSegment)
                          .where(SubtitleSegment.video_id == vid)).scalars().all()
    assert len(segs) >= 1 and segs[0].text == "ciao mondo."


def test_run_transcribe_canceling_skips_auto_export(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"sorgente finta")
    vid = _seed_video(status=VideoStatus.UPLOADED, stored_path=str(src),
                      duration=10.0, auto_export=True, auto_silence=False,
                      auto_retakes=False, auto_speedup=False)
    # annullamento richiesto PRIMA che il worker processi il job
    jid = _seed_job(vid, jtype=JobType.TRANSCRIBE, status=JobStatus.CANCELING)
    monkeypatch.setattr(transcribe_mod, "transcribe_words", _fake_transcribe_words)

    worker.run_job(jid)

    video = _video(vid)
    assert video.status == VideoStatus.REVIEW  # la trascrizione e' comunque salvata
    assert _job(jid).status == JobStatus.CANCELED
    with SessionLocal() as db:  # ... ma NESSUN export viene accodato
        exports = db.execute(select(Job).where(
            Job.video_id == vid, Job.type == JobType.EXPORT)).scalars().all()
    assert exports == []
