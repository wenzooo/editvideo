"""Chaos engineering: fault injection DETERMINISTICA (nessun server live).

Ogni test inietta un guasto controllato (ffmpeg/ffprobe che fallisce o va in
timeout, disco pieno, DB 'database is locked', file di input troncato, whisper
che solleva) e ASSERISCE che il sistema DEGRADI con grazia — errore tipizzato e
chiaro, nessun file di output parziale lasciato su disco, job in ERROR con
messaggio, worker VIVO — invece di crashare o corrompere lo stato.

Ambiente isolato configurato PRIMA di importare l'app (stesso preambolo degli
altri moduli worker: `setdefault` con valori propri, cosi' l'ordine di import
non rompe nulla — get_settings e' @lru_cache e il primo importatore vince). A
runtime si usano SEMPRE i percorsi di get_settings(), mai _TMP. Ogni test
ripulisce le righe DB che crea (il DB e' condiviso con il resto della suite).

Due test iniettano un guasto ffmpeg REALE (file troncato, sorgente spazzatura):
sono marcati skip se ffmpeg/ffprobe non sono nel PATH.
"""
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_chaos_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

import app.routers.videos as videos_router  # noqa: E402
from app import worker  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Job, JobStatus, JobType, SubtitleSegment, Video, VideoStatus,
)
from app.security import get_login_rate_limiter  # noqa: E402
from app.services import ffmpeg as ff  # noqa: E402
from app.services import transcribe as transcribe_mod  # noqa: E402

init_db()
get_settings().ensure_dirs()
client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker

_HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
_needs_ffmpeg = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe non nel PATH")


# --------------------------------------------------------------------------- #
# seeding + cleanup (DB condiviso con il resto della suite)
# --------------------------------------------------------------------------- #
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


@pytest.fixture(autouse=True)
def _reset_breaker():
    """Il breaker Whisper e' stato di modulo: azzerato attorno a ogni test cosi'
    un test che lo apre (fallimenti ripetuti) non contamina i successivi."""
    worker._whisper_breaker.reset()
    yield
    worker._whisper_breaker.reset()


def _seed_video(**kw) -> str:
    defaults = dict(original_name="clip.mp4", stored_path="/nonexistent/clip.mp4",
                    status=VideoStatus.UPLOADED, duration=5.0, cuts=[], speedups=[],
                    has_audio=False, auto_export=False, auto_silence=False,
                    auto_retakes=False, auto_speedup=False)
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


def _auth() -> dict:
    get_login_rate_limiter().clear()  # niente 429 residui da altri moduli
    r = client.post("/api/auth/login", json={"password": get_settings().admin_password},
                    headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# =========================================================================== #
# 1) ffprobe che fallisce / va in timeout / riceve un file troncato
# =========================================================================== #
def test_probe_nonzero_returncode_raises_clear_ffmpegerror(monkeypatch):
    """ffprobe con returncode != 0 -> FFmpegError tipizzata con messaggio, mai un
    errore opaco. Path inesistente -> nessuna cache: gira _probe_uncached."""
    def fake_run(cmd, timeout=120):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Invalid data found")

    monkeypatch.setattr(ff, "_run", fake_run)
    with pytest.raises(ff.FFmpegError) as ei:
        ff.probe("/nonexistent/ev_chaos_probe_rc.mp4")
    assert "ffprobe fallito" in str(ei.value)


def test_probe_timeout_surfaces_as_timeoutexpired(monkeypatch):
    """ffprobe che si impianta -> subprocess.TimeoutExpired (errore tipizzato e
    catchabile dal chiamante), non un blocco infinito. L'upload lo assorbe nel
    suo `except Exception` trasformandolo in una voce di errore per-file."""
    def fake_run(cmd, timeout=120):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(ff, "_run", fake_run)
    with pytest.raises(subprocess.TimeoutExpired):
        ff.probe("/nonexistent/ev_chaos_probe_to.mp4")


@_needs_ffmpeg
def test_probe_truncated_real_file_raises_ffmpegerror(tmp_path):
    """Fault injection REALE: file spazzatura/troncato -> ffprobe vero fallisce e
    probe alza FFmpegError chiara, non un 500 opaco ne' un dict malformato."""
    bad = tmp_path / "troncato.mp4"
    bad.write_bytes(b"\x00\x01NON-E-UN-VIDEO" * 64)
    with pytest.raises(ff.FFmpegError):
        ff.probe(bad)


# =========================================================================== #
# 2) ffmpeg export che fallisce -> nessun output parziale, job ERROR, worker vivo
# =========================================================================== #
class _FakePopen:
    """Finto subprocess.Popen: simula ffmpeg che termina con codice != 0
    scrivendo un errore su stderr e senza emettere righe di progresso."""

    def __init__(self, cmd, stdout=None, stderr=None, text=None, **kw):
        self.returncode = 1
        self.stdout = iter(())  # nessuna riga -> il loop di lettura esce subito
        if stderr is not None and hasattr(stderr, "write"):
            stderr.write("Invalid data found when processing input\n")
            stderr.flush()

    def wait(self):
        return self.returncode

    def kill(self):  # usato solo dal watchdog (che qui non scatta)
        self.returncode = -9


def test_export_video_nonzero_returncode_raises_and_leaves_no_output(monkeypatch, tmp_path):
    """ff.export_video con ffmpeg a codice != 0 -> FFmpegError con codice+coda di
    stderr, e nessun file di output creato/lasciato su disco."""
    monkeypatch.setattr(ff.subprocess, "Popen", _FakePopen)
    src = tmp_path / "src.mp4"
    src.write_bytes(b"sorgente finta")
    dst = tmp_path / "out.mp4"

    with pytest.raises(ff.FFmpegError) as ei:
        ff.export_video(str(src), str(dst), [(0.0, 5.0)], None, False)
    assert "export fallito" in str(ei.value) and "codice 1" in str(ei.value)
    assert not dst.exists()  # nessun output parziale


def _fake_export_writes_partial_then(exc: Exception, seen: dict):
    """Sostituto di ff.export_video: scrive un output PARZIALE su disco (come farebbe
    ffmpeg interrotto a meta') e poi solleva `exc`. Serve a verificare che il
    chiamante ripulisca il parziale."""
    def fake(src, dst, plan, ass_path, has_audio,
             progress_cb=None, intro_zoom=False, fps=30.0):
        Path(dst).write_bytes(b"PARZIALE-NON-VALIDO")
        seen["existed_mid"] = Path(dst).exists()
        raise exc
    return fake


def test_export_ffmpeg_failure_removes_partial_and_marks_error(monkeypatch, tmp_path):
    """Guasto ffmpeg in export: il parziale su disco viene rimosso, il job va in
    ERROR con messaggio, il video in ERROR, e run_job NON solleva (worker vivo)."""
    src = tmp_path / "src.mp4"
    src.write_bytes(b"sorgente finta")
    vid = _seed_video(status=VideoStatus.EXPORTING, stored_path=str(src), duration=5.0)
    jid = _seed_job(vid, jtype=JobType.EXPORT, status=JobStatus.RUNNING)

    seen: dict = {}
    monkeypatch.setattr(worker.ff, "export_video",
                        _fake_export_writes_partial_then(
                            ff.FFmpegError("export fallito (codice 1): boom"), seen))

    worker.run_job(jid)  # non deve sollevare

    dst = get_settings().exports_dir / f"{vid}.mp4"
    assert seen.get("existed_mid") is True   # il parziale c'era durante l'export...
    assert not dst.exists()                  # ...ed e' stato rimosso al fallimento
    job = _job(jid)
    assert job.status == JobStatus.ERROR and job.error
    assert _video(vid).status == VideoStatus.ERROR


@_needs_ffmpeg
def test_export_real_ffmpeg_on_garbage_source_marks_error_no_output(tmp_path):
    """Fault injection REALE: sorgente spazzatura -> ffmpeg vero fallisce, run_job
    chiude il job in ERROR (video ERROR), nessun MP4 di output resta su disco, il
    worker resta vivo."""
    src = tmp_path / "garbage.mp4"
    src.write_bytes(b"questo-non-e-un-video" * 128)
    vid = _seed_video(status=VideoStatus.EXPORTING, stored_path=str(src),
                      duration=5.0, has_audio=False)
    jid = _seed_job(vid, jtype=JobType.EXPORT, status=JobStatus.RUNNING)

    worker.run_job(jid)  # non deve sollevare

    dst = get_settings().exports_dir / f"{vid}.mp4"
    assert not dst.exists()  # nessun output (parziale o vuoto) lasciato
    job = _job(jid)
    assert job.status == JobStatus.ERROR and job.error
    assert _video(vid).status == VideoStatus.ERROR and _video(vid).error_message


# =========================================================================== #
# 3) disco pieno (OSError) durante export e durante upload
# =========================================================================== #
def test_export_disk_full_oserror_cleans_up_and_marks_error(monkeypatch, tmp_path):
    """OSError(ENOSPC) durante l'export -> parziale rimosso, job ERROR col messaggio
    di disco pieno, video ERROR, worker vivo (run_job non propaga)."""
    src = tmp_path / "src.mp4"
    src.write_bytes(b"sorgente finta")
    vid = _seed_video(status=VideoStatus.EXPORTING, stored_path=str(src), duration=5.0)
    jid = _seed_job(vid, jtype=JobType.EXPORT, status=JobStatus.RUNNING)

    seen: dict = {}
    monkeypatch.setattr(worker.ff, "export_video",
                        _fake_export_writes_partial_then(
                            OSError(28, "No space left on device"), seen))

    worker.run_job(jid)

    dst = get_settings().exports_dir / f"{vid}.mp4"
    assert seen.get("existed_mid") is True
    assert not dst.exists()
    job = _job(jid)
    assert job.status == JobStatus.ERROR
    assert job.error and "space" in job.error.lower()
    assert _video(vid).status == VideoStatus.ERROR


def test_upload_disk_full_no_residual_file(monkeypatch):
    """Disco pieno durante la scrittura dell'upload (open/write -> OSError): la
    richiesta risponde 200 con l'errore per-file, nessun video committato e NESSUN
    file parziale lasciato in originals_dir (rollback + unlink)."""
    real_open = open
    created: list[Path] = []

    def fake_open(path, mode="r", *a, **kw):
        # solo la scrittura del file di upload usa open() dentro videos.py; il file
        # viene creato davvero (parziale) e la prima write fallisce come a disco pieno.
        if "w" in mode:
            fh = real_open(path, mode, *a, **kw)
            created.append(Path(path))

            class _Full:
                def __enter__(self_):
                    return self_

                def __exit__(self_, *e):
                    fh.close()
                    return False

                def write(self_, _b):
                    raise OSError(28, "No space left on device")

            return _Full()
        return real_open(path, mode, *a, **kw)

    # `open` non e' un nome definito nel modulo: lo si inietta come globale del
    # modulo (raising=False), cosi' le funzioni di videos.py lo risolvono per prime.
    monkeypatch.setattr(videos_router, "open", fake_open, raising=False)

    originals = get_settings().originals_dir
    before = set(originals.glob("*"))
    r = client.post("/api/videos/upload",
                    files=[("files", ("pieno.mp4", b"x" * 4096, "video/mp4"))],
                    headers=_auth())

    assert r.status_code == 200          # l'app non crasha: errore per-file
    body = r.json()
    assert body["created"] == []
    assert len(body["errors"]) == 1
    assert "space" in body["errors"][0]["reason"].lower()
    assert created and not created[0].exists()   # il parziale e' stato rimosso
    assert set(originals.glob("*")) == before     # nessun residuo in originals_dir


# =========================================================================== #
# 4) DB 'database is locked' / OperationalError transitorio -> worker non muore
# =========================================================================== #
def test_worker_loop_survives_operationalerror_in_claim(monkeypatch):
    """OperationalError ('database is locked') nel claim -> il worker_loop la
    assorbe e NON muore (l'iterazione seguente esce per stop_event)."""
    stop = threading.Event()
    raised = {"n": 0}

    def boom():
        raised["n"] += 1
        stop.set()  # dopo questa iterazione il loop terminera'
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    monkeypatch.setattr(worker, "claim_next_job", boom)
    monkeypatch.setattr(worker, "run_retention_sweep", lambda: None)

    worker.worker_loop(stop_event=stop)  # non deve sollevare: worker vivo
    assert raised["n"] == 1               # il guasto e' stato iniettato e assorbito


def test_persist_job_error_swallows_db_errors(monkeypatch):
    """La registrazione dell'errore e' best-effort: se anche la scrittura su DB
    fallisce (lock), _persist_job_error NON solleva -> la coda non si blocca e il
    worker resta vivo."""
    def boom(*a, **kw):
        raise OperationalError("UPDATE jobs", {}, Exception("database is locked"))

    monkeypatch.setattr(worker, "SessionLocal", boom)
    # nessuna eccezione deve uscire nonostante il DB irraggiungibile
    worker._persist_job_error("job-inesistente", "video-inesistente", "messaggio")


# =========================================================================== #
# 5) trascrizione whisper che solleva -> degradazione graziosa
# =========================================================================== #
def test_repeated_whisper_failures_open_breaker_and_fast_degrade(monkeypatch):
    """Fallimenti ripetuti del modello principale (download corrotto sul disco
    effimero di HF): dopo la soglia il circuit breaker si APRE e le trascrizioni
    successive degradano SUBITO al fallback senza piu' riprovare il principale."""
    primary_calls = {"n": 0}

    def fake(path, duration, progress_cb=None, model_name=None):
        if model_name is None:                       # modello principale: sempre rotto
            primary_calls["n"] += 1
            raise RuntimeError("modello principale corrotto sul disco effimero")
        return ([(0.0, 0.5, " ok")], [])             # fallback: funziona (degrado ok)

    monkeypatch.setattr(transcribe_mod, "transcribe_words", fake)
    monkeypatch.setattr(get_settings(), "job_retry_backoff_seconds", 0.0)  # test veloce

    threshold = worker._WHISPER_BREAKER_THRESHOLD
    for _ in range(threshold):
        words = worker._transcribe_with_resilience("jobX", "/fake.mp4", 5.0)
        assert words                                  # degrada sempre col fallback

    assert worker._whisper_breaker.allow() is False   # soglia raggiunta -> aperto
    calls_when_open = primary_calls["n"]

    # con il circuito aperto il principale NON viene piu' chiamato: degrado immediato
    words = worker._transcribe_with_resilience("jobX", "/fake.mp4", 5.0)
    assert words
    assert primary_calls["n"] == calls_when_open      # principale saltato
