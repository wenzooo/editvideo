"""Test di sicurezza (iterazione 20): rate limit del login, security header
compatibili con l'iframe, estrazione IP e non-fuga dei segreti.

Due livelli:
- PURI/offline: RateLimiter e client_ip (nessun import di app.main);
- INTEGRAZIONE: TestClient su app.main SENZA lifespan (niente worker/DB) per
  verificare header su tutte le risposte e il 429 del login. L'ambiente è
  configurato via env PRIMA di importare app.main: SECRET_KEY esplicita evita
  qualsiasi scrittura su disco (resolved_secret non genera il file).
"""
import os

os.environ["ADMIN_PASSWORD"] = "correct-horse-battery"
os.environ["SECRET_KEY"] = "unit-test-secret-key-0123456789abcdef"

from starlette.requests import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402  -> verifica implicita che `import app.main` sia ok
from app.security import (  # noqa: E402
    SECURITY_HEADERS,
    RateLimiter,
    client_ip,
    get_login_rate_limiter,
)

ADMIN_PW = "correct-horse-battery"
SECRET = "unit-test-secret-key-0123456789abcdef"


# --------------------------------------------------------------------------- #
# RateLimiter (puro, con clock iniettato)
# --------------------------------------------------------------------------- #
def test_ratelimiter_allows_below_threshold():
    rl = RateLimiter(max_attempts=3, window_seconds=10)
    assert rl.is_blocked("ip", now=0) is False
    rl.record("ip", now=0)
    rl.record("ip", now=1)
    assert rl.is_blocked("ip", now=2) is False  # 2 < 3


def test_ratelimiter_blocks_at_threshold():
    rl = RateLimiter(max_attempts=3, window_seconds=10)
    for _ in range(3):
        rl.record("ip", now=0)
    assert rl.is_blocked("ip", now=1) is True   # 3 >= 3


def test_ratelimiter_window_slides_and_frees_slots():
    rl = RateLimiter(max_attempts=2, window_seconds=10)
    rl.record("ip", now=0)
    rl.record("ip", now=0)
    assert rl.is_blocked("ip", now=5) is True
    # oltre la finestra: i timestamp vecchi vengono scartati
    assert rl.is_blocked("ip", now=11) is False


def test_ratelimiter_reset_clears_key():
    rl = RateLimiter(max_attempts=1, window_seconds=10)
    rl.record("ip", now=0)
    assert rl.is_blocked("ip", now=0) is True
    rl.reset("ip")
    assert rl.is_blocked("ip", now=0) is False


def test_ratelimiter_keys_are_independent():
    rl = RateLimiter(max_attempts=1, window_seconds=10)
    rl.record("a", now=0)
    assert rl.is_blocked("a", now=0) is True
    assert rl.is_blocked("b", now=0) is False  # IP diverso: secchiello separato


def test_ratelimiter_retry_after_within_window():
    rl = RateLimiter(max_attempts=1, window_seconds=10)
    rl.record("ip", now=0)
    ra = rl.retry_after("ip", now=1)
    assert 1 <= ra <= 10
    assert rl.retry_after("nope", now=1) == 0


def test_ratelimiter_clamps_degenerate_config():
    rl = RateLimiter(max_attempts=0, window_seconds=0)
    assert rl.max_attempts >= 1 and rl.window_seconds >= 1


# --------------------------------------------------------------------------- #
# client_ip (dietro reverse-proxy)
# --------------------------------------------------------------------------- #
def _req(headers: dict | None = None, client=("10.0.0.1", 0)) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": client})


def test_client_ip_prefers_x_forwarded_for():
    r = _req({"X-Forwarded-For": "203.0.113.7, 70.41.3.18"}, client=("10.0.0.1", 0))
    assert client_ip(r) == "203.0.113.7"


def test_client_ip_uses_x_real_ip_when_no_xff():
    r = _req({"X-Real-IP": "198.51.100.9"}, client=("10.0.0.1", 0))
    assert client_ip(r) == "198.51.100.9"


def test_client_ip_falls_back_to_socket_peer():
    assert client_ip(_req(client=("192.0.2.5", 0))) == "192.0.2.5"


def test_client_ip_handles_missing_client():
    assert client_ip(_req(client=None)) == "unknown"


# --------------------------------------------------------------------------- #
# Security headers via middleware (compatibili iframe)
# --------------------------------------------------------------------------- #
_client = TestClient(app)  # niente `with`: no lifespan -> no worker/DB


def test_security_headers_present_on_all_responses():
    r = _client.get("/api/auth/me")  # endpoint pubblico, 200
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Permissions-Policy" in r.headers
    assert "Content-Security-Policy" in r.headers


def test_no_x_frame_options_and_frame_ancestors_permissive():
    r = _client.get("/api/auth/me")
    # NIENTE X-Frame-Options: romperebbe l'embedding nell'iframe di HF
    assert "X-Frame-Options" not in r.headers
    csp = r.headers["Content-Security-Policy"]
    # frame-ancestors permissivo (*), MAI restrittivo (self/none)
    assert "frame-ancestors *" in csp
    assert "frame-ancestors 'self'" not in csp
    assert "frame-ancestors 'none'" not in csp


def test_security_headers_also_on_error_responses():
    # anche una risposta 401 porta gli header di sicurezza
    r = _client.get("/api/videos")
    assert r.status_code == 401
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "X-Frame-Options" not in r.headers


def test_security_headers_constant_has_no_x_frame_options():
    assert "X-Frame-Options" not in SECURITY_HEADERS
    assert "frame-ancestors *" in SECURITY_HEADERS["Content-Security-Policy"]


# --------------------------------------------------------------------------- #
# Login: rate limit + login legittimo + non-fuga dei segreti
# --------------------------------------------------------------------------- #
def test_legit_login_still_works():
    get_login_rate_limiter().clear()
    r = _client.post("/api/auth/login", json={"password": ADMIN_PW},
                     headers={"X-Forwarded-For": "11.11.11.11"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["token"]


def test_login_rate_limit_triggers_429_after_threshold():
    limiter = get_login_rate_limiter()
    limiter.clear()
    ip = {"X-Forwarded-For": "22.22.22.22"}
    n = limiter.max_attempts
    # i primi N tentativi errati -> 401
    for _ in range(n):
        r = _client.post("/api/auth/login", json={"password": "wrong"}, headers=ip)
        assert r.status_code == 401
    # superata la soglia -> 429 con Retry-After
    r = _client.post("/api/auth/login", json={"password": "wrong"}, headers=ip)
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert int(r.headers["Retry-After"]) >= 1
    # anche la password GIUSTA resta bloccata finché la finestra non si libera
    r = _client.post("/api/auth/login", json={"password": ADMIN_PW}, headers=ip)
    assert r.status_code == 429


def test_login_success_resets_counter_for_legit_user():
    limiter = get_login_rate_limiter()
    limiter.clear()
    ip = {"X-Forwarded-For": "33.33.33.33"}
    # qualche errore sotto soglia, poi login riuscito
    for _ in range(max(1, limiter.max_attempts - 1)):
        assert _client.post("/api/auth/login", json={"password": "wrong"},
                            headers=ip).status_code == 401
    assert _client.post("/api/auth/login", json={"password": ADMIN_PW},
                        headers=ip).status_code == 200
    # il successo ha azzerato il contatore: nuovo margine pieno
    assert limiter.is_blocked("33.33.33.33") is False


def test_different_ips_do_not_share_the_limit():
    limiter = get_login_rate_limiter()
    limiter.clear()
    # esaurisce il margine per un IP
    for _ in range(limiter.max_attempts + 1):
        _client.post("/api/auth/login", json={"password": "wrong"},
                     headers={"X-Forwarded-For": "44.44.44.44"})
    # un altro IP (utente legittimo) NON è influenzato
    r = _client.post("/api/auth/login", json={"password": ADMIN_PW},
                     headers={"X-Forwarded-For": "55.55.55.55"})
    assert r.status_code == 200


def test_login_responses_do_not_leak_secrets():
    limiter = get_login_rate_limiter()
    limiter.clear()
    ip = {"X-Forwarded-For": "66.66.66.66"}
    # 401 su password errata: nessun eco di password/secret
    r = _client.post("/api/auth/login", json={"password": "totally-wrong"}, headers=ip)
    assert r.status_code == 401
    blob = r.text.lower()
    assert "totally-wrong" not in blob
    assert ADMIN_PW.lower() not in blob
    assert SECRET.lower() not in blob
    # 429 dopo la soglia: idem
    for _ in range(limiter.max_attempts + 2):
        r = _client.post("/api/auth/login", json={"password": "x"}, headers=ip)
    assert r.status_code == 429
    blob = r.text.lower()
    assert ADMIN_PW.lower() not in blob and SECRET.lower() not in blob
