"""QA auth: token HMAC (make_token/verify_token/_sign), _extract_token e
endpoint /api/auth/* (login/me/logout) di backend/app/auth.py.

Tutto OFFLINE, nessun ffmpeg. Non duplica test_security.py (rate-limit 429 e
security headers gia' coperti li'): qui si coprono il ciclo di vita del token,
le priorita' di estrazione (Bearer > cookie > ?t=) e la semantica degli endpoint.

Ambiente isolato configurato PRIMA di importare l'app (stesso preambolo dei
moduli esistenti: setdefault non sovrascrive nulla di gia' impostato, quindi
in suite completa vincono le env del primo modulo importato — per questo a
runtime si usa SEMPRE get_settings().resolved_secret(), mai le costanti locali).

Include 1 TEST ROSSO che documenta un bug confermato (QA-02): asserisce il
comportamento CORRETTO atteso e oggi fallisce. Non va "aggiustato".
Include 1 characterization (QA-11): logout senza revoca server-side del token.
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_qa_auth_tokens_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import (  # noqa: E402
    COOKIE_NAME,
    _extract_token,
    _sign,
    make_token,
    verify_token,
)
from app.config import get_settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import get_login_rate_limiter  # noqa: E402

init_db()  # idempotente: serve solo per l'endpoint protetto /api/videos
get_settings().ensure_dirs()

client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker
ADMIN_PW = "correct-horse-battery"
SECRET = "qa-explicit-secret-for-pure-token-tests"  # per le funzioni PURE


@pytest.fixture(autouse=True)
def _clean_limiter_and_cookies():
    """Nessun residuo tra test e verso gli altri moduli della suite: ogni login
    fallito viene ripulito (altrimenti gli altri moduli ricevono 429) e il
    cookie jar del client condiviso viene svuotato."""
    get_login_rate_limiter().clear()
    client.cookies.clear()
    yield
    get_login_rate_limiter().clear()
    client.cookies.clear()


# --------------------------------------------------------------------------- #
# 1. Token puri: make_token / verify_token (secret esplicito, nessuna app)
# --------------------------------------------------------------------------- #
def test_token_roundtrip_valid():
    token = make_token(SECRET, days=1)
    assert verify_token(SECRET, token) is True


def test_token_expired_is_rejected():
    # exp nel passato ma firma GIUSTA: deve fallire solo per la scadenza
    exp = str(int(time.time()) - 10)
    token = f"{exp}.{_sign(SECRET, exp)}"
    assert verify_token(SECRET, token) is False


def test_token_tampered_signature_is_rejected():
    token = make_token(SECRET, days=1)
    exp, sig = token.rsplit(".", 1)
    flipped = ("0" if sig[-1] != "0" else "1")
    assert verify_token(SECRET, f"{exp}.{sig[:-1]}{flipped}") is False


def test_token_without_dot_is_rejected():
    assert verify_token(SECRET, "nodotatall") is False


def test_token_empty_or_none_is_rejected():
    assert verify_token(SECRET, "") is False
    assert verify_token(SECRET, None) is False


def test_token_non_numeric_exp_is_rejected():
    # firma corretta su payload non numerico: int("abc") -> ValueError -> False
    token = f"abc.{_sign(SECRET, 'abc')}"
    assert verify_token(SECRET, token) is False


def test_token_wrong_secret_is_rejected():
    token = make_token(SECRET, days=1)
    assert verify_token("another-secret-entirely", token) is False


# --------------------------------------------------------------------------- #
# 2. _extract_token: priorita' Bearer > cookie > ?t= (stub di Request)
# --------------------------------------------------------------------------- #
def _stub(auth: str | None = None, cookie: str | None = None, qp: str | None = None):
    headers = {"authorization": auth} if auth is not None else {}
    cookies = {COOKIE_NAME: cookie} if cookie is not None else {}
    query = {"t": qp} if qp is not None else {}
    return SimpleNamespace(headers=headers, cookies=cookies, query_params=query)


def test_extract_bearer_wins_over_cookie_and_query():
    req = _stub(auth="Bearer tok-header", cookie="tok-cookie", qp="tok-query")
    assert _extract_token(req) == "tok-header"


def test_extract_cookie_wins_over_query_without_bearer():
    req = _stub(cookie="tok-cookie", qp="tok-query")
    assert _extract_token(req) == "tok-cookie"


def test_extract_query_param_alone_works():
    assert _extract_token(_stub(qp="tok-query")) == "tok-query"


def test_extract_nothing_returns_none():
    assert _extract_token(_stub()) is None


def test_extract_non_bearer_scheme_falls_back():
    # "Basic xyz" NON e' Bearer: si passa al cookie (e poi a ?t=)
    assert _extract_token(_stub(auth="Basic xyz", cookie="tok-cookie")) == "tok-cookie"
    assert _extract_token(_stub(auth="Basic xyz", qp="tok-query")) == "tok-query"
    assert _extract_token(_stub(auth="Basic xyz")) is None


def test_extract_bearer_without_token_characterization():
    # Characterization del comportamento attuale:
    # - "Bearer" secco (senza spazio) NON matcha "bearer " -> fallback al cookie;
    # - "Bearer " con token vuoto matcha e ritorna "" (stringa vuota, non None),
    #   che verify_token tratta comunque come non valido.
    assert _extract_token(_stub(auth="Bearer", cookie="tok-cookie")) == "tok-cookie"
    assert _extract_token(_stub(auth="Bearer ", cookie="tok-cookie")) == ""


# --------------------------------------------------------------------------- #
# 3. Endpoint /api/auth/* (TestClient senza lifespan)
# --------------------------------------------------------------------------- #
def _login(ip: str = "203.0.113.201") -> tuple[str, object]:
    r = client.post("/api/auth/login", json={"password": ADMIN_PW},
                    headers={"X-Forwarded-For": ip})
    assert r.status_code == 200
    return r.json()["token"], r


def test_login_ok_returns_token_and_httponly_cookie():
    token, r = _login()
    body = r.json()
    assert body["ok"] is True and token
    # il token emesso e' verificabile con il secret RUNTIME (mai costanti locali)
    assert verify_token(get_settings().resolved_secret(), token) is True
    set_cookie = r.headers.get("set-cookie", "")
    assert COOKIE_NAME + "=" in set_cookie
    assert "httponly" in set_cookie.lower()


def test_me_with_bearer_is_authenticated():
    token, _ = _login()
    client.cookies.clear()  # solo Bearer, nessun cookie residuo del login
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["authenticated"] is True


def test_me_with_expired_token_characterization():
    # /me non alza mai 401: risponde 200 con authenticated=False (leggi auth.me).
    # Un endpoint PROTETTO (require_auth) invece risponde 401.
    secret = get_settings().resolved_secret()
    exp = str(int(time.time()) - 10)
    expired = f"{exp}.{_sign(secret, exp)}"
    hdr = {"Authorization": f"Bearer {expired}"}
    r = client.get("/api/auth/me", headers=hdr)
    assert r.status_code == 200
    assert r.json()["authenticated"] is False
    r2 = client.get("/api/videos", headers=hdr)
    assert r2.status_code == 401


def test_cookie_only_authentication_works():
    token, _ = _login()
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, token)  # SOLO cookie, nessun header
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["authenticated"] is True
    # anche un endpoint protetto da require_auth accetta il solo cookie
    r2 = client.get("/api/videos")
    assert r2.status_code == 200


def test_logout_revokes_token_including_bearer():
    # QA-11 aggiornato dopo il fix di sicurezza #3: il logout ora REVOCA il token
    # lato server (incrementa la "generazione" firmata nel token), quindi anche un
    # Bearer gia' emesso smette di valere. Il logout cancella pure il cookie client.
    token, _ = _login()
    r = client.post("/api/auth/logout")
    assert r.status_code == 200 and r.json()["ok"] is True
    set_cookie = r.headers.get("set-cookie", "")
    assert COOKIE_NAME + "=" in set_cookie
    low = set_cookie.lower()
    assert "max-age=0" in low or "expires=" in low  # cookie di cancellazione
    client.cookies.clear()
    # il token Bearer emesso PRIMA del logout NON e' piu' valido (revocato)
    r2 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.json()["authenticated"] is False
    r3 = client.get("/api/videos", headers={"Authorization": f"Bearer {token}"})
    assert r3.status_code == 401


# --------------------------------------------------------------------------- #
# 4. TEST ROSSO — password non-ASCII al login
# --------------------------------------------------------------------------- #
def test_login_with_non_ascii_password_returns_401():
    # BUG CONFERMATO QA-02: password non-ASCII al login -> 500 invece di 401 —
    # vedi TEST_REPORT.md. auth.py:76 usa hmac.compare_digest su str: con input
    # non-ASCII ("pässword") alza TypeError -> 500 Internal Server Error, e il
    # tentativo NON viene contato dal rate limiter (limiter.record mai raggiunto).
    # Comportamento CORRETTO atteso: 401 credenziali errate (e tentativo contato).
    noraise = TestClient(app, raise_server_exceptions=False)
    r = noraise.post("/api/auth/login", json={"password": "pässword"},
                     headers={"X-Forwarded-For": "203.0.113.202"})
    assert r.status_code == 401  # oggi: 500 (TypeError da hmac.compare_digest)
