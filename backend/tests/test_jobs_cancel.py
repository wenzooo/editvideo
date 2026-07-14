"""Test del cancel dei job (iterazione 16): dequeue logico dei job 'queued',
richiesta di annullamento sui 'running', skip del worker sui 'canceled'.

Ambiente isolato configurato PRIMA di importare l'app (come test_security):
DB sqlite temporaneo + segreti espliciti. `setdefault` non sovrascrive nulla di
già impostato da altri moduli/CI, quindi l'ordine di import non rompe né questi
test né gli altri.
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_jobs_cancel_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app import worker  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402  -> verifica implicita che `import app.main` sia ok
from app.models import Job, JobStatus, JobType, Video, VideoStatus  # noqa: E402
from app.security import get_login_rate_limiter  # noqa: E402

init_db()
client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker


def _token() -> str:
    get_login_rate_limiter().clear()
    r = client.post("/api/auth/login", json={"password": get_settings().admin_password},
                    headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 200
    return r.json()["token"]


def _auth() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


def _make_job(status: str, jtype: str = JobType.EXPORT,
              vstatus: str = VideoStatus.READY) -> tuple[str, str]:
    with SessionLocal() as db:
        v = Video(original_name="clip.mp4", stored_path="/tmp/clip.mp4", status=vstatus)
        db.add(v)
        db.flush()
        j = Job(video_id=v.id, type=jtype, status=status)
        db.add(j)
        db.commit()
        return v.id, j.id


# --------------------------------------------------------------------------- #
# endpoint: queued -> canceled (dequeue logico)
# --------------------------------------------------------------------------- #
def test_cancel_queued_marks_canceled():
    _vid, jid = _make_job(JobStatus.QUEUED)
    r = client.post(f"/api/jobs/{jid}/cancel", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "job_id": jid, "status": "canceled", "canceled": True}
    with SessionLocal() as db:
        assert db.get(Job, jid).status == JobStatus.CANCELED


def test_cancel_queued_keeps_video_state_coherent():
    # il video non è ancora stato toccato dall'handler: resta 'ready' (nessun
    # 'exporting' orfano dopo l'annullamento di un job in coda)
    vid, jid = _make_job(JobStatus.QUEUED, vstatus=VideoStatus.READY)
    client.post(f"/api/jobs/{jid}/cancel", headers=_auth())
    with SessionLocal() as db:
        assert db.get(Video, vid).status == VideoStatus.READY


# --------------------------------------------------------------------------- #
# worker: il loop salta i job 'canceled' e non va in errore
# --------------------------------------------------------------------------- #
def test_worker_claim_skips_canceled():
    with SessionLocal() as db:  # isolamento: nessun altro job in coda
        db.execute(delete(Job))
        db.commit()
    _vid, jid = _make_job(JobStatus.CANCELED)
    assert worker.claim_next_job() is None  # lo salta, nessuna eccezione
    with SessionLocal() as db:
        assert db.get(Job, jid).status == JobStatus.CANCELED  # invariato


def test_worker_claim_picks_queued_but_not_canceled():
    with SessionLocal() as db:
        db.execute(delete(Job))
        db.commit()
    _c, canceled_id = _make_job(JobStatus.CANCELED)
    _q, queued_id = _make_job(JobStatus.QUEUED)
    claimed = worker.claim_next_job()
    assert claimed == queued_id and claimed != canceled_id


# --------------------------------------------------------------------------- #
# endpoint: running -> canceling
# --------------------------------------------------------------------------- #
def test_cancel_running_marks_canceling():
    _vid, jid = _make_job(JobStatus.RUNNING)
    r = client.post(f"/api/jobs/{jid}/cancel", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["status"] == "canceling" and body["canceled"] is False
    with SessionLocal() as db:
        assert db.get(Job, jid).status == JobStatus.CANCELING


def test_cancel_requested_helper_reflects_status():
    _v1, canceling_id = _make_job(JobStatus.CANCELING)
    _v2, running_id = _make_job(JobStatus.RUNNING)
    with SessionLocal() as db:
        assert worker._cancel_requested(db, canceling_id) is True
        assert worker._cancel_requested(db, running_id) is False


# --------------------------------------------------------------------------- #
# worker: un job 'canceling' viene chiuso come 'canceled' (non 'done'), no crash
# --------------------------------------------------------------------------- #
def test_worker_closes_canceling_job_as_canceled(monkeypatch):
    _vid, jid = _make_job(JobStatus.RUNNING, jtype="noop")
    with SessionLocal() as db:  # l'utente annulla mentre il job gira
        db.get(Job, jid).status = JobStatus.CANCELING
        db.commit()
    monkeypatch.setitem(worker.HANDLERS, "noop", lambda job_id, video_id: None)
    worker.run_job(jid)  # non deve sollevare
    with SessionLocal() as db:
        assert db.get(Job, jid).status == JobStatus.CANCELED  # non 'done'


# --------------------------------------------------------------------------- #
# endpoint: casi limite (idempotenza, 404, auth)
# --------------------------------------------------------------------------- #
def test_cancel_done_job_is_idempotent_noop():
    _vid, jid = _make_job(JobStatus.DONE)
    r = client.post(f"/api/jobs/{jid}/cancel", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["status"] == "done" and body["canceled"] is False
    with SessionLocal() as db:
        assert db.get(Job, jid).status == JobStatus.DONE


def test_cancel_unknown_job_404():
    r = client.post("/api/jobs/nonesistente/cancel", headers=_auth())
    assert r.status_code == 404


def test_cancel_requires_auth():
    _vid, jid = _make_job(JobStatus.QUEUED)
    client.cookies.clear()  # scarta il cookie di sessione lasciato dai login precedenti
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 401
