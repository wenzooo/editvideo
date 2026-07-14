"""Test dell'osservabilita' (workstream observability):

- Request ID: presente nell'header di risposta e rispettato se fornito dal client;
- /api/health non autenticato: minimale ({"ok": true}), nessun leak diagnostico;
- /api/health autenticato: payload profondo con db/disk/queue quando health_deep;
- /api/metrics autenticato: contatori corretti (unit sul servizio + delta via API);
- /api/metrics senza auth: 401.

Ambiente isolato configurato PRIMA di importare l'app (get_settings() e'
@lru_cache): stesso preambolo env-first dei moduli esistenti. NB: il DB sqlite
puo' essere condiviso con altri moduli nella suite completa (env congelato dal
primo import), quindi i test via API usano assert basati sul DELTA rispetto a un
baseline; la correttezza esatta dei contatori e' verificata a parte con
collect_metrics su un engine isolato.
"""
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_observability_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Job, JobStatus, JobType, Video, VideoStatus  # noqa: E402
from app.security import get_login_rate_limiter  # noqa: E402
from app.services.metrics import collect_metrics  # noqa: E402

init_db()
get_settings().ensure_dirs()
client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker


def _auth() -> dict:
    get_login_rate_limiter().clear()
    r = client.post("/api/auth/login", json={"password": get_settings().admin_password},
                    headers={"X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------------------------------------------------------- request id ---
def test_request_id_generated_when_absent():
    header = get_settings().request_id_header
    r = client.get("/api/health")
    assert r.status_code == 200
    rid = r.headers.get(header)
    assert rid  # generato dal server quando il client non lo fornisce
    assert len(rid) >= 8


def test_request_id_is_echoed_when_provided():
    header = get_settings().request_id_header
    r = client.get("/api/health", headers={header: "corr-12345"})
    assert r.status_code == 200
    assert r.headers.get(header) == "corr-12345"  # rispettato, non rigenerato


def test_request_id_present_on_error_responses():
    header = get_settings().request_id_header
    # 404 su una risorsa API inesistente: l'header di correlazione c'e' comunque
    r = client.get("/api/jobs/does-not-exist", headers=_auth())
    assert r.status_code == 404
    assert r.headers.get(header)


# -------------------------------------------------------------------- health ---
def test_health_unauthenticated_is_minimal():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}  # nessun leak diagnostico senza auth


def test_health_authenticated_deep_has_dependency_checks(monkeypatch):
    # forza il ramo profondo indipendentemente dallo stato condiviso della cache
    monkeypatch.setattr(get_settings(), "health_deep", True)
    # ffmpeg/ffprobe potrebbero non essere nel PATH (job "veloce" della CI, senza
    # binari nativi): li forziamo presenti cosi' ok dipende solo dalla logica,
    # non dall'ambiente. In locale con ffmpeg installato l'esito e' identico.
    monkeypatch.setattr("app.routers.jobs.shutil.which", lambda name: f"/usr/bin/{name}")
    r = client.get("/api/health", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True  # db raggiungibile + ffmpeg/ffprobe nel PATH
    assert isinstance(body["db"], bool) and body["db"] is True
    assert isinstance(body["ffmpeg"], bool)
    assert isinstance(body["ffprobe"], bool)
    assert set(body["disk"]) == {"media_root_free_mb", "data_dir_free_mb"}
    assert set(body["queue"]) == {"queued", "running"}
    assert isinstance(body["queue"]["queued"], int)
    assert isinstance(body["queue"]["running"], int)


def test_health_authenticated_shallow_when_deep_disabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "health_deep", False)
    r = client.get("/api/health", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    # payload leggero: niente controlli profondi
    assert set(body) == {"ok", "version", "ffmpeg", "ffprobe", "whisper_model", "language"}


# ------------------------------------------------------------------- metrics ---
def test_metrics_requires_auth():
    r = client.get("/api/metrics")
    assert r.status_code == 401


def test_metrics_404_when_disabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_enabled", False)
    r = client.get("/api/metrics", headers=_auth())
    assert r.status_code == 404


def test_metrics_reflects_seeded_rows_via_delta(monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_enabled", True)
    before = client.get("/api/metrics", headers=_auth()).json()

    # semina righe note: 2 video 'ready', 1 job 'queued', 1 job 'done' (5s)
    with SessionLocal() as db:
        v1 = Video(original_name="a.mp4", stored_path="/tmp/a.mp4", status=VideoStatus.READY)
        v2 = Video(original_name="b.mp4", stored_path="/tmp/b.mp4", status=VideoStatus.READY)
        db.add_all([v1, v2])
        db.flush()
        started = datetime(2024, 1, 1, 12, 0, 0)
        db.add_all([
            Job(video_id=v1.id, type=JobType.EXPORT, status=JobStatus.QUEUED),
            Job(video_id=v2.id, type=JobType.TRANSCRIBE, status=JobStatus.DONE,
                started_at=started, finished_at=started + timedelta(seconds=5)),
        ])
        db.commit()

    after = client.get("/api/metrics", headers=_auth()).json()

    def _jd(m, status):  # delta job per stato
        return m["jobs"]["by_status"].get(status, 0)

    assert _jd(after, JobStatus.QUEUED) - _jd(before, JobStatus.QUEUED) == 1
    assert _jd(after, JobStatus.DONE) - _jd(before, JobStatus.DONE) == 1
    assert after["jobs"]["total"] - before["jobs"]["total"] == 2
    assert after["jobs"]["queued"] - before["jobs"]["queued"] == 1
    vd = (after["videos"]["by_status"].get(VideoStatus.READY, 0)
          - before["videos"]["by_status"].get(VideoStatus.READY, 0))
    assert vd == 2
    assert after["videos"]["total"] - before["videos"]["total"] == 2
    # una durata media dei job completati esiste ed e' positiva
    assert isinstance(after["avg_done_job_seconds"], (int, float))
    assert after["avg_done_job_seconds"] > 0


def test_collect_metrics_exact_counts_on_isolated_db():
    """Correttezza ESATTA dei contatori su un DB dedicato in memoria (nessuna
    interferenza da altri moduli della suite che condividono il DB dell'app)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as db:
        v_ready = Video(original_name="r.mp4", stored_path="/tmp/r.mp4", status=VideoStatus.READY)
        v_err = Video(original_name="e.mp4", stored_path="/tmp/e.mp4", status=VideoStatus.ERROR)
        db.add_all([v_ready, v_err])
        db.flush()
        t0 = datetime(2024, 1, 1, 0, 0, 0)
        db.add_all([
            Job(video_id=v_ready.id, type=JobType.EXPORT, status=JobStatus.QUEUED),
            Job(video_id=v_ready.id, type=JobType.EXPORT, status=JobStatus.RUNNING),
            Job(video_id=v_ready.id, type=JobType.TRANSCRIBE, status=JobStatus.DONE,
                started_at=t0, finished_at=t0 + timedelta(seconds=4)),
            Job(video_id=v_err.id, type=JobType.EXPORT, status=JobStatus.DONE,
                started_at=t0, finished_at=t0 + timedelta(seconds=8)),
        ])
        db.commit()

        m = collect_metrics(db)

    assert m["jobs"]["by_status"] == {
        JobStatus.QUEUED: 1, JobStatus.RUNNING: 1, JobStatus.DONE: 2,
    }
    assert m["jobs"]["total"] == 4
    assert m["jobs"]["queued"] == 1
    assert m["jobs"]["running"] == 1
    assert m["videos"]["by_status"] == {VideoStatus.READY: 1, VideoStatus.ERROR: 1}
    assert m["videos"]["total"] == 2
    # media di 4s e 8s = 6s
    assert m["avg_done_job_seconds"] == 6.0


def test_collect_metrics_avg_none_without_done_jobs():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as db:
        v = Video(original_name="x.mp4", stored_path="/tmp/x.mp4", status=VideoStatus.UPLOADED)
        db.add(v)
        db.flush()
        db.add(Job(video_id=v.id, type=JobType.EXPORT, status=JobStatus.QUEUED))
        db.commit()
        m = collect_metrics(db)
    assert m["avg_done_job_seconds"] is None
