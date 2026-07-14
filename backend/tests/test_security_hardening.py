"""Test dell'hardening di sicurezza (workstream "defense in depth").

Copre le aggiunte a ``app.security``:
- ``get_write_rate_limiter``: rate limiter GENERICO e riutilizzabile (stessa
  classe del login, riusata DRY). Gli endpoint mutanti pesanti (``/api/batch/*``)
  lo usano tramite ``rate_limit_batch`` con una CHIAVE GLOBALE costante (freno non
  aggirabile ruotando X-Forwarded-For). Blocca oltre soglia con 429 + Retry-After
  e si libera quando la finestra scorre;
- gli header di sicurezza (defense in depth) restano presenti su OGNI risposta,
  senza rompere l'iframe di HF (nessun X-Frame-Options, frame-ancestors *);
- il rate limiter del LOGIN continua a funzionare identico ed è un singleton
  DISTINTO da quello degli endpoint mutanti (nessuna regressione / contaminazione).

Ambiente isolato configurato PRIMA di importare l'app (get_settings è
@lru_cache), come negli altri moduli di test. ``UPLOAD_RATE_MAX`` è messo basso
per rendere il test del flood veloce e deterministico.
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_sec_hardening_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test
# soglia bassa: il test del flood sugli enqueue di massa resta rapido.
os.environ.setdefault("UPLOAD_RATE_MAX", "3")
os.environ.setdefault("UPLOAD_RATE_WINDOW_SECONDS", "60")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402  -> verifica implicita che `import app.main` sia ok
from app.models import Job  # noqa: E402
from app.security import (  # noqa: E402
    SECURITY_HEADERS,
    RateLimiter,
    get_login_rate_limiter,
    get_write_rate_limiter,
)

init_db()
get_settings().ensure_dirs()
client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker

BATCH_TRANSCRIBE = "/api/batch/transcribe"


@pytest.fixture(autouse=True)
def _reset_limiters():
    """Ogni test parte con ENTRAMBI i limiter puliti (stato in-memory condiviso
    tra i test perché sono singleton lru_cache)."""
    get_login_rate_limiter().clear()
    get_write_rate_limiter().clear()
    yield
    get_login_rate_limiter().clear()
    get_write_rate_limiter().clear()


@pytest.fixture(autouse=True)
def _cleanup_jobs():
    """Nella suite completa il DB è condiviso (get_settings è @lru_cache: vince
    l'env del primo modulo che importa l'app). Gli endpoint /api/batch possono
    quindi accodare job su video lasciati da altri moduli: qui si eliminano SOLO
    i Job creati dai test di questo file, per non inquinare la coda condivisa."""
    with SessionLocal() as db:
        before = {jid for (jid,) in db.execute(select(Job.id))}
    yield
    with SessionLocal() as db:
        for (jid,) in db.execute(select(Job.id)):
            if jid not in before:
                obj = db.get(Job, jid)
                if obj is not None:
                    db.delete(obj)
        db.commit()


def _auth() -> dict:
    # il login usa il SUO limiter (separato da quello degli enqueue): il clear
    # evita che residui blocchino il login e non intacca il budget delle scritture.
    get_login_rate_limiter().clear()
    r = client.post("/api/auth/login", json={"password": get_settings().admin_password},
                    headers={"X-Forwarded-For": "7.7.7.7"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --------------------------------------------------------------------------- #
# 1. Rate limiter generico (uso "conta ogni richiesta"): soglia + finestra
# --------------------------------------------------------------------------- #
def test_generic_limiter_blocks_over_threshold_and_frees_in_window():
    """Semantica dell'uso generico (record a ogni richiesta, nessun reset):
    esattamente ``max_attempts`` richieste passano, la successiva è bloccata; poi
    quando la finestra scorre gli slot si liberano."""
    rl = RateLimiter(max_attempts=3, window_seconds=10)
    # 3 richieste "contate" sotto soglia: nessun blocco al momento del check
    for t in range(3):
        assert rl.is_blocked("ip", now=t) is False
        rl.record("ip", now=t)
    # la 4a (ancora dentro la finestra) è bloccata
    assert rl.is_blocked("ip", now=3) is True
    # oltre la finestra i timestamp vecchi decadono: torna a passare
    assert rl.is_blocked("ip", now=11) is False


def test_write_limiter_is_configured_from_upload_settings():
    # niente valori hardcoded: nella suite completa vince l'env del primo modulo
    # che ha importato l'app, quindi si confronta con le impostazioni EFFETTIVE.
    s = get_settings()
    wl = get_write_rate_limiter()
    assert wl.max_attempts == s.upload_rate_max
    assert wl.window_seconds == float(s.upload_rate_window_seconds)
    # tetto chiavi condiviso col login (anti DoS di memoria)
    assert wl.max_keys == s.rate_limit_max_keys


def test_write_and_login_limiters_are_distinct_singletons():
    """Il limiter degli enqueue è un singleton DIVERSO da quello del login:
    esaurire l'uno non tocca l'altro (nessuna contaminazione di stato)."""
    s = get_settings()
    wl = get_write_rate_limiter()
    ll = get_login_rate_limiter()
    assert wl is not ll
    # ciascuno prende le proprie soglie dalla configurazione (davvero separati)
    assert wl.max_attempts == s.upload_rate_max
    assert ll.max_attempts == s.login_max_attempts
    # il limiter generico non ha freno globale (le rotte sono autenticate),
    # a differenza del login che tiene anche un contatore globale anti-bruteforce
    assert wl.global_max_attempts == 0
    assert ll.global_max_attempts >= 1


# --------------------------------------------------------------------------- #
# 2. Il limiter generico protegge gli enqueue di massa (/api/batch)
# --------------------------------------------------------------------------- #
def test_batch_enqueue_flood_gets_429_with_retry_after():
    headers = _auth()
    limit = get_settings().upload_rate_max
    # le prime N richieste passano (200 OK, a prescindere da quanti video accoda)
    for _ in range(limit):
        assert client.post(BATCH_TRANSCRIBE, headers=headers).status_code == 200
    # oltre soglia -> 429 con Retry-After
    r = client.post(BATCH_TRANSCRIBE, headers=headers)
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 1


def test_batch_flood_recovers_after_limiter_reset():
    """Quando la finestra si libera (qui simulata con clear()) l'endpoint torna a
    rispondere: il blocco è temporaneo, non permanente."""
    headers = _auth()
    for _ in range(get_settings().upload_rate_max + 1):
        client.post(BATCH_TRANSCRIBE, headers=headers)
    assert client.post(BATCH_TRANSCRIBE, headers=headers).status_code == 429
    get_write_rate_limiter().clear()  # la finestra è scorsa
    assert client.post(BATCH_TRANSCRIBE, headers=headers).status_code == 200


def test_all_batch_enqueue_routes_are_rate_limited():
    """Il limiter è applicato a LIVELLO DI ROUTER: tutte le rotte di enqueue di
    massa lo condividono (un flood misto le satura tutte)."""
    headers = _auth()
    routes = ["/api/batch/transcribe", "/api/batch/export",
              "/api/batch/auto", "/api/batch/export-reviewed"]
    limit = get_settings().upload_rate_max
    for i in range(limit):
        assert client.post(routes[i % len(routes)], headers=headers).status_code == 200
    # esaurito il budget condiviso, QUALSIASI rotta batch risponde 429
    for route in routes:
        assert client.post(route, headers=headers).status_code == 429


def test_batch_flood_not_bypassable_via_x_forwarded_for_rotation():
    """Regression (SECURITY): il freno sugli enqueue di massa e' GLOBALE, non
    per-IP. Ruotando ``X-Forwarded-For`` (spoofabile dietro il proxy HF) a ogni
    richiesta NON si aggira il tetto: tutte le richieste cadono nella stessa chiave
    globale, quindi oltre soglia scatta comunque il 429. Col vecchio freno per-IP
    ogni XFF diverso apriva una chiave nuova con 0 hit -> bypass illimitato."""
    headers = _auth()
    limit = get_settings().upload_rate_max
    # esaurisce il budget usando un IP spoofato DIVERSO a ogni richiesta
    for i in range(limit):
        h = {**headers, "X-Forwarded-For": f"10.0.0.{i}"}
        assert client.post(BATCH_TRANSCRIBE, headers=h).status_code == 200
    # ancora un IP mai visto: col fix e' bloccato (freno globale), non un bypass
    h = {**headers, "X-Forwarded-For": "203.0.113.9"}
    assert client.post(BATCH_TRANSCRIBE, headers=h).status_code == 429


def test_unauthenticated_batch_does_not_consume_write_budget():
    """require_auth gira PRIMA di rate_limit_writes: gli anonimi prendono 401
    senza intaccare il budget (least privilege). Dopo un flood anonimo, un utente
    autenticato ha ancora margine pieno."""
    for _ in range(get_settings().upload_rate_max + 5):
        assert client.post(BATCH_TRANSCRIBE).status_code == 401
    # l'IP non ha registrato scritture: il limiter è ancora vuoto per "testclient"
    assert len(get_write_rate_limiter()._hits) == 0
    # e un utente autenticato passa regolarmente
    assert client.post(BATCH_TRANSCRIBE, headers=_auth()).status_code == 200


# --------------------------------------------------------------------------- #
# 3. Header di sicurezza (defense in depth) — regression guard
# --------------------------------------------------------------------------- #
def test_security_headers_present_and_iframe_safe():
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Permissions-Policy" in r.headers
    # minimale ma senza rompere la riproduzione: NON deve disattivare autoplay
    assert "autoplay" not in r.headers["Permissions-Policy"]
    # iframe HF: nessun X-Frame-Options, frame-ancestors permissivo
    assert "X-Frame-Options" not in r.headers
    assert "frame-ancestors *" in r.headers["Content-Security-Policy"]


def test_security_headers_on_429_from_write_limiter():
    """Anche la risposta 429 del rate limiter porta gli header di sicurezza
    (il middleware security_headers resta il più esterno)."""
    headers = _auth()
    for _ in range(get_settings().upload_rate_max + 1):
        r = client.post(BATCH_TRANSCRIBE, headers=headers)
    assert r.status_code == 429
    for key, value in SECURITY_HEADERS.items():
        assert r.headers.get(key) == value


# --------------------------------------------------------------------------- #
# 4. Il rate limiter del LOGIN continua a funzionare identico (no regressione)
# --------------------------------------------------------------------------- #
def test_login_rate_limit_still_triggers_429():
    limiter = get_login_rate_limiter()
    limiter.clear()
    ip = {"X-Forwarded-For": "88.88.88.88"}
    for _ in range(limiter.max_attempts):
        assert client.post("/api/auth/login", json={"password": "wrong"},
                           headers=ip).status_code == 401
    r = client.post("/api/auth/login", json={"password": "wrong"}, headers=ip)
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_login_success_still_resets_and_write_flood_does_not_affect_login():
    """Il successo del login azzera il suo contatore (utente legittimo mai punito)
    e un flood sugli enqueue NON blocca il login: limiter separati."""
    # satura il limiter delle scritture
    headers = _auth()
    for _ in range(get_settings().upload_rate_max + 2):
        client.post(BATCH_TRANSCRIBE, headers=headers)
    assert client.post(BATCH_TRANSCRIBE, headers=headers).status_code == 429
    # il login resta pienamente funzionante (usa un altro limiter)
    get_login_rate_limiter().clear()
    r = client.post("/api/auth/login",
                    json={"password": get_settings().admin_password},
                    headers={"X-Forwarded-For": "99.99.99.99"})
    assert r.status_code == 200
    assert get_login_rate_limiter().is_blocked("99.99.99.99") is False
