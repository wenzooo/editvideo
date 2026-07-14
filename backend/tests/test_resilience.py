"""Test della resilienza: retry+backoff, circuit breaker (puri) e la loro
integrazione nel worker (retry del modello Whisper, fallback una-tantum,
degradazione graziosa a export senza sottotitoli).

Ambiente isolato configurato PRIMA di importare l'app (stesso preambolo degli
altri moduli worker: `setdefault` con valori propri, cosi' l'ordine di import
non rompe nulla — get_settings e' @lru_cache e il primo importatore vince). A
runtime si usano SEMPRE i percorsi di get_settings(). Nessuna chiamata HTTP:
si esercita direttamente app.worker / app.services.resilience. Ogni test
ripulisce le righe DB che crea (DB condiviso con il resto della suite).
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_resilience_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

import pytest  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app import worker  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Job, JobStatus, JobType, SubtitleSegment, Video, VideoStatus,
)
from app.services import transcribe as transcribe_mod  # noqa: E402
from app.services.resilience import CircuitBreaker, retry_call  # noqa: E402

init_db()


# ========================================================================== #
# 1) retry_call — puro, sleeper mockato
# ========================================================================== #
def test_retry_call_retries_then_raises():
    sleeps: list[float] = []
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("transitorio")

    with pytest.raises(ValueError):
        retry_call(boom, attempts=3, backoff=2.0, sleeper=sleeps.append)

    assert calls["n"] == 3               # riprova esattamente 'attempts' volte
    assert sleeps == [2.0, 4.0]          # backoff esponenziale: 2*2**0, 2*2**1


def test_retry_call_succeeds_on_second_attempt():
    sleeps: list[float] = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("primo colpo a vuoto")
        return "ok"

    assert retry_call(flaky, attempts=3, backoff=1.5, sleeper=sleeps.append) == "ok"
    assert calls["n"] == 2               # si ferma appena riesce
    assert sleeps == [1.5]               # una sola attesa (prima del 2° tentativo)


def test_retry_call_does_not_retry_unlisted_exception():
    sleeps: list[float] = []
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise KeyError("non transitorio")

    with pytest.raises(KeyError):
        retry_call(boom, attempts=5, backoff=1.0,
                   retry_on=(ValueError,), sleeper=sleeps.append)

    assert calls["n"] == 1               # fail-fast: nessun retry
    assert sleeps == []


def test_retry_call_backoff_zero_no_sleep():
    sleeps: list[float] = []

    def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        retry_call(boom, attempts=3, backoff=0.0, sleeper=sleeps.append)
    assert sleeps == []                  # backoff 0 -> mai attese


# ========================================================================== #
# 2) CircuitBreaker — puro, clock mockato
# ========================================================================== #
def test_circuit_breaker_opens_after_threshold_and_blocks():
    clock = {"t": 0.0}
    cb = CircuitBreaker(threshold=2, cooldown=10.0, clock=lambda: clock["t"])

    assert cb.allow() is True
    cb.record_failure()
    assert cb.allow() is True            # 1 fallimento < soglia
    cb.record_failure()
    assert cb.allow() is False           # soglia raggiunta -> aperto (blocca)

    clock["t"] = 5.0
    assert cb.allow() is False           # cooldown non ancora scaduto
    clock["t"] = 10.0
    assert cb.allow() is True            # half-open: cooldown scaduto, un tentativo


def test_circuit_breaker_success_resets():
    cb = CircuitBreaker(threshold=2, cooldown=100.0, clock=lambda: 0.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.allow() is False
    cb.record_success()
    assert cb.allow() is True            # un successo richiude il circuito


def test_circuit_breaker_counts_only_consecutive_failures():
    cb = CircuitBreaker(threshold=2, cooldown=100.0, clock=lambda: 0.0)
    cb.record_failure()
    cb.record_success()                  # azzera il conteggio
    cb.record_failure()
    assert cb.allow() is True            # solo 1 fallimento consecutivo: resta chiuso


# ========================================================================== #
# 3) Integrazione worker — seeding DB minimale + cleanup
# ========================================================================== #
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
    """Il breaker del modello Whisper e' uno stato di modulo: azzerato attorno a
    ogni test per non far dipendere un test dagli esiti del precedente."""
    worker._whisper_breaker.reset()
    yield
    worker._whisper_breaker.reset()


def _seed_video(**kw) -> str:
    defaults = dict(original_name="clip.mp4", stored_path="/nonexistent/clip.mp4",
                    status=VideoStatus.UPLOADED, duration=10.0, cuts=[], speedups=[],
                    auto_export=False, auto_silence=False, auto_retakes=False,
                    auto_speedup=False)
    defaults.update(kw)
    with SessionLocal() as db:
        v = Video(**defaults)
        db.add(v)
        db.commit()
        _created_videos.append(v.id)
        return v.id


def _seed_job(video_id: str, jtype: str = JobType.TRANSCRIBE,
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


def _segments(vid: str) -> list[SubtitleSegment]:
    with SessionLocal() as db:
        return db.execute(select(SubtitleSegment)
                          .where(SubtitleSegment.video_id == vid)).scalars().all()


def _make_transcribe(calls: list, fail_models: set):
    """Fake di transcribe_words: registra il model_name di OGNI chiamata e
    fallisce per i modelli in ``fail_models`` (None = modello principale)."""
    def fake(path, duration, progress_cb=None, model_name=None):
        calls.append(model_name)
        if model_name in fail_models:
            raise RuntimeError(f"modello {model_name!r} rotto")
        return ([(0.5, 1.0, " ciao"), (1.1, 1.6, " mondo.")], [])
    return fake


# --- 3a) fallback modello invocato UNA SOLA VOLTA -------------------------- #
def test_fallback_model_invoked_once(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"sorgente finta")
    vid = _seed_video(stored_path=str(src))
    jid = _seed_job(vid)

    calls: list = []
    monkeypatch.setattr(transcribe_mod, "transcribe_words", _make_transcribe(calls, {None}))
    monkeypatch.setattr(get_settings(), "job_retry_backoff_seconds", 0.0)  # test veloce

    worker.run_job(jid)

    fb = get_settings().whisper_fallback_model
    assert calls.count(fb) == 1                    # fallback: UNA sola volta
    assert calls[-1] == fb and all(m is None for m in calls[:-1])  # prima solo il principale
    assert _video(vid).status == VideoStatus.REVIEW
    assert _job(jid).status == JobStatus.DONE
    assert len(_segments(vid)) >= 1                # le parole del fallback sono salvate


# --- 3b) breaker aperto -> salta il principale, degrada subito al fallback -- #
def test_open_breaker_skips_primary_uses_fallback(monkeypatch):
    calls: list = []
    monkeypatch.setattr(transcribe_mod, "transcribe_words", _make_transcribe(calls, {None}))

    for _ in range(worker._WHISPER_BREAKER_THRESHOLD):
        worker._whisper_breaker.record_failure()   # apre il circuito
    assert worker._whisper_breaker.allow() is False

    words, fallback = worker._transcribe_with_resilience("jobX", "/fake/path.mp4", 10.0)

    assert calls == [get_settings().whisper_fallback_model]  # principale MAI chiamato
    assert words                                             # il fallback ha prodotto parole


# --- 3c) degradazione: trascrizione fallita -> export SENZA subs se flag on -- #
def test_degradation_exports_without_subs_when_flag_on(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"sorgente finta")
    vid = _seed_video(stored_path=str(src))
    jid = _seed_job(vid)

    calls: list = []
    fb = get_settings().whisper_fallback_model
    monkeypatch.setattr(transcribe_mod, "transcribe_words",
                        _make_transcribe(calls, {None, fb}))  # principale E fallback KO
    monkeypatch.setattr(get_settings(), "job_retry_backoff_seconds", 0.0)
    monkeypatch.setattr(get_settings(), "export_allow_without_subs", True)

    worker.run_job(jid)

    job = _job(jid)
    assert job.status == JobStatus.DONE and job.error is None  # NON va in ERROR
    video = _video(vid)
    assert video.status == VideoStatus.REVIEW                  # esportabile...
    assert video.error_message is None                         # ...stato coerente
    assert _segments(vid) == []                                # ...ma senza sottotitoli


# --- 3d) degradazione disattivata -> ERROR bloccante ---------------------- #
def test_degradation_disabled_marks_error(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"sorgente finta")
    vid = _seed_video(stored_path=str(src))
    jid = _seed_job(vid)

    calls: list = []
    fb = get_settings().whisper_fallback_model
    monkeypatch.setattr(transcribe_mod, "transcribe_words",
                        _make_transcribe(calls, {None, fb}))
    monkeypatch.setattr(get_settings(), "job_retry_backoff_seconds", 0.0)
    monkeypatch.setattr(get_settings(), "export_allow_without_subs", False)

    worker.run_job(jid)

    job = _job(jid)
    assert job.status == JobStatus.ERROR and job.error
    video = _video(vid)
    assert video.status == VideoStatus.ERROR and video.error_message
