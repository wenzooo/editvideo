"""QA API test: /api/health a due livelli (SECURITY_REPORT #8).

- NON autenticato: solo {"ok": true} — utilizzabile come uptime-check, ma senza
  versione/modello/lingua/binari (niente fingerprinting).
- Autenticato (Bearer valido): payload diagnostico completo, incluso "ffprobe".

Ambiente isolato configurato PRIMA di importare l'app (get_settings() e'
@lru_cache): stesso preambolo env-first dei moduli esistenti.
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_health_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import get_login_rate_limiter  # noqa: E402
from app.version import APP_VERSION  # noqa: E402

init_db()
get_settings().ensure_dirs()
client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker


def _auth() -> dict:
    # solo login riusciti: il clear evita 429 residui di altri moduli
    get_login_rate_limiter().clear()
    r = client.post("/api/auth/login", json={"password": get_settings().admin_password},
                    headers={"X-Forwarded-For": "8.8.4.4"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_health_unauthenticated_is_minimal():
    r = client.get("/api/health")
    assert r.status_code == 200
    # SOLO {"ok": true}: nessuna chiave diagnostica esposta senza auth
    assert r.json() == {"ok": True}


def test_health_unauthenticated_with_bogus_token_is_minimal():
    r = client.get("/api/health", headers={"Authorization": "Bearer non.valido.xyz"})
    assert r.status_code == 200  # mai 401: resta un uptime-check
    assert r.json() == {"ok": True}


def test_health_authenticated_returns_full_payload(monkeypatch):
    # Il job "veloce" della CI gira senza binari nativi (ffmpeg/ffprobe non nel
    # PATH), mentre l'health profondo mette ok=False se mancano. Qui verifichiamo
    # la FORMA del payload, non l'ambiente: forziamo i binari presenti cosi' ok
    # e' deterministico ovunque (in locale con ffmpeg passa gia').
    monkeypatch.setattr("app.routers.jobs.shutil.which", lambda name: f"/usr/bin/{name}")
    r = client.get("/api/health", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["version"] == APP_VERSION
    assert isinstance(body["ffmpeg"], bool)
    assert isinstance(body["ffprobe"], bool)
    s = get_settings()
    assert body["whisper_model"] == s.whisper_model
    assert body["language"] == (s.whisper_language or "auto")
    # I campi diagnostici di base sono SEMPRE presenti quando autenticati; con
    # health_deep attivo (default) il payload ne aggiunge altri (db/disk/queue),
    # quindi si verifica l'inclusione, non l'uguaglianza stretta.
    assert {"ok", "version", "ffmpeg", "ffprobe", "whisper_model", "language"} <= set(body)
