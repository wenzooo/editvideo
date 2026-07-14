"""Test DIMOSTRATIVI dell'audit di sicurezza (sessione "occhio da attaccante").

Questi test provano vulnerabilita' REALI del codice di produzione. Vedi
``SECURITY_REPORT.md`` (root del repo) per scenario d'attacco e fix proposto di
ciascun finding.

Convenzione usata in questo file
--------------------------------
* ``test_vuln_*``  -> VERDI: asseriscono il comportamento **vulnerabile attuale**.
  Passano oggi (documentano fedelmente la falla). Quando la falla verra' corretta
  questi test andranno AGGIORNATI/INVERTITI (il commento indica cosa cambiare).
* ``test_secure_*`` -> marcati ``xfail(strict=False)``: asseriscono il
  comportamento **sicuro atteso** (post-fix). Oggi FALLISCONO -> pytest li segna
  come ``xfailed`` (ROSSI-PER-DESIGN, ma NON rompono la suite). Quando il fix
  arrivera' diventeranno ``xpass``, segnalando che la mitigazione e' in atto.
* ``test_safe_*`` -> VERDI: confermano una difesa che REGGE (regression guard).

L'ambiente e' configurato via env PRIMA di importare l'app (get_settings e'
@lru_cache): SECRET_KEY esplicita evita qualsiasi scrittura su disco.
Per l'audit del 500 si usa TestClient(app, raise_server_exceptions=False).
"""
import os

os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")

import hmac  # noqa: E402
import stat  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import app  # noqa: E402
from app.security import get_login_rate_limiter  # noqa: E402

ADMIN_PW = "correct-horse-battery"

# raise_server_exceptions=False: cosi' un 500 (500 lato server) ci arriva come
# risposta HTTP invece di risollevare l'eccezione nel test.
client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Ogni test parte con il rate limiter del login pulito (stato in-memory)."""
    get_login_rate_limiter().clear()
    yield
    get_login_rate_limiter().clear()


# =========================================================================== #
# CRITICO #1 — Password non-ASCII: RISOLTA in master (QA-02, commit 6e63a9b).
#   auth.py ora confronta su BYTES (encode) -> una password errata non-ASCII
#   torna 401 e viene conteggiata dal rate limiter. I test qui sotto asseriscono
#   il comportamento SICURO (erano "vuln-demo" contro la versione pre-fix).
# =========================================================================== #
def test_nonascii_password_returns_401_after_qa02_fix():
    """RISOLTA (QA-02): con il confronto su bytes, una password errata con
    caratteri non-ASCII torna 401 (non piu' 500/TypeError)."""
    r = client.post(
        "/api/auth/login",
        json={"password": "pässwörd"},
        headers={"X-Forwarded-For": "203.0.113.10"},
    )
    assert r.status_code == 401


def test_nonascii_password_failure_is_rate_limited_after_qa02_fix():
    """RISOLTA (QA-02): il tentativo fallito non-ASCII viene CONTATO dal rate
    limiter, quindi oltre la soglia l'IP viene bloccato."""
    ip = "203.0.113.11"
    for _ in range(50):
        client.post(
            "/api/auth/login",
            json={"password": "éèìò"},
            headers={"X-Forwarded-For": ip},
        )
    # ora il tentativo e' conteggiato e l'IP risulta bloccato
    assert get_login_rate_limiter()._hits.get(ip) is not None
    assert get_login_rate_limiter().is_blocked(ip) is True


def test_vuln_nonascii_admin_password_would_lock_out_all_logins():
    """VULN (self-lockout / availability): se ADMIN_PASSWORD stessa contiene
    caratteri non-ASCII, OGNI login -- anche con la password GIUSTA -- crolla in
    ``hmac.compare_digest`` con TypeError -> l'app diventa non-loginabile.

    Dimostrato sul meccanismo esatto usato in auth.py:76."""
    with pytest.raises(TypeError):
        hmac.compare_digest("bjørn-secret", "bjørn-secret")


def test_secure_nonascii_password_should_return_401():
    r = client.post(
        "/api/auth/login",
        json={"password": "pässwörd"},
        headers={"X-Forwarded-For": "203.0.113.12"},
    )
    assert r.status_code == 401


# =========================================================================== #
# CRITICO #2 — Rate limit del login aggirabile ruotando X-Forwarded-For
#   + crescita illimitata delle chiavi del RateLimiter (DoS memoria)
#   security.py:65-69 (client_ip si fida ciecamente di XFF/X-Real-IP)
# =========================================================================== #
def test_rotating_xff_bruteforce_is_blocked_by_global_limit():
    """RISOLTA (#2): anche ruotando X-Forwarded-For a ogni richiesta, i tentativi
    confluiscono nel contatore GLOBALE, quindi oltre la soglia scatta il 429: il
    bypass del rate-limit e' chiuso."""
    limiter = get_login_rate_limiter()
    n = limiter.max_attempts * 3  # ben oltre la soglia
    codes = set()
    for i in range(n):
        r = client.post(
            "/api/auth/login",
            json={"password": "wrong"},
            headers={"X-Forwarded-For": f"198.51.100.{i % 256}, 10.0.0.1"},
        )
        codes.add(r.status_code)
    assert 429 in codes  # il freno globale scatta nonostante la rotazione XFF


def test_ratelimiter_memory_is_bounded_under_spoofed_xff_flood():
    """RISOLTA (#2): un flusso di IP fittizi via XFF non fa piu' crescere la
    memoria senza tetto. Il freno globale blocca presto (429), quindi solo pochi
    IP registrano una chiave; in ogni caso ``max_keys`` limita il dict."""
    limiter = get_login_rate_limiter()
    distinct = 400
    for i in range(distinct):
        client.post(
            "/api/auth/login",
            json={"password": "wrong"},
            headers={"X-Forwarded-For": f"10.{i // 256}.{i % 256}.7"},
        )
    # niente piu' una chiave per ogni IP fittizio: crescita LIMITATA
    assert len(limiter._hits) < distinct
    assert len(limiter._hits) <= limiter.global_max_attempts


def test_secure_login_should_block_bruteforce_despite_xff_rotation():
    limiter = get_login_rate_limiter()
    last = 200
    for i in range(limiter.max_attempts * 3):
        last = client.post(
            "/api/auth/login",
            json={"password": "wrong"},
            headers={"X-Forwarded-For": f"198.51.100.{i % 256}"},
        ).status_code
    assert last == 429


# =========================================================================== #
# ALTO #3 — Token via query string, logout senza revoca, cookie senza Secure
#   auth.py:53 (_extract_token legge ?t=), auth.py:89 (logout cancella solo cookie)
# =========================================================================== #
def _login_token(ip: str = "192.0.2.50") -> str:
    return client.post(
        "/api/auth/login", json={"password": ADMIN_PW},
        headers={"X-Forwarded-For": ip},
    ).json()["token"]


def test_vuln_token_accepted_in_query_string():
    """VULN: il token completo viaggia in ``?t=<token>`` (per <video>/<img> nel
    l'iframe HF). Finisce negli access log del proxy/uvicorn, nella history del
    browser e nei Referer -> esposizione del segreto di sessione."""
    token = _login_token()
    r = client.get(f"/api/auth/me?t={token}")
    assert r.json() == {"authenticated": True}


def test_logout_revokes_token_after_fix():
    """RISOLTA (#3): il logout incrementa la "generazione" dei token, quindi il
    token emesso prima smette di valere lato server (revoca reale, non solo cookie)."""
    token = _login_token()
    client.post("/api/auth/logout")
    r = client.get(f"/api/auth/me?t={token}")
    assert r.json() == {"authenticated": False}  # revocato dal logout


def test_login_cookie_has_secure_flag_after_fix():
    """RISOLTA (#3): il cookie di sessione ora porta il flag ``Secure``."""
    r = client.post(
        "/api/auth/login", json={"password": ADMIN_PW},
        headers={"X-Forwarded-For": "192.0.2.51"},
    )
    set_cookie = r.headers.get("set-cookie", "")
    assert "ev_session=" in set_cookie
    assert "secure" in set_cookie.lower()


def test_secure_logout_should_revoke_token():
    token = _login_token()
    client.post("/api/auth/logout")
    r = client.get(f"/api/auth/me?t={token}")
    assert r.json() == {"authenticated": False}


def test_secure_login_cookie_should_have_secure_flag():
    r = client.post(
        "/api/auth/login", json={"password": ADMIN_PW},
        headers={"X-Forwarded-For": "192.0.2.52"},
    )
    assert "secure" in r.headers.get("set-cookie", "").lower()


# =========================================================================== #
# MEDIO #4 — resolved_secret(): file secret.key IRROBUSTITO (fix applicato)
#   config.py: chmod 0600, scrittura atomica, file vuoto rigenerato, cache
# =========================================================================== #
def test_empty_secret_key_file_is_regenerated_after_fix(tmp_path):
    """FIX: un ``data/secret.key`` VUOTO (write interrotto, disco pieno, race
    web/worker) NON produce piu' una chiave vuota: viene rigenerata una chiave
    vera e persistita al posto del file corrotto."""
    (tmp_path / "secret.key").write_text("")  # file vuoto
    s = Settings(secret_key="", data_dir=tmp_path)
    key = s.resolved_secret()
    assert len(key) >= 32  # chiave vera, mai stringa vuota
    # e il file e' stato riparato: una seconda Settings legge la stessa chiave
    s2 = Settings(secret_key="", data_dir=tmp_path)
    assert s2.resolved_secret() == key


def test_secret_key_file_created_owner_only_after_fix(tmp_path):
    """FIX: ``resolved_secret`` crea ``data/secret.key`` con permessi 0600
    (solo owner) anche con umask permissivo: la chiave di firma HMAC non e'
    leggibile da altri utenti locali."""
    old_umask = os.umask(0)  # umask permissivo per rendere il test deterministico
    try:
        s = Settings(secret_key="", data_dir=tmp_path)
        key = s.resolved_secret()
        assert key  # e' stata generata una chiave
        mode = os.stat(tmp_path / "secret.key").st_mode
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(mode) == 0o600, f"mode={oct(stat.S_IMODE(mode))} (atteso 0600)"


def test_secret_key_is_cached_and_stable_per_settings(tmp_path):
    """FIX: la chiave e' cache-ata in memoria (niente read_text a ogni richiesta)
    e stabile: chiamate ripetute sulla stessa Settings ritornano lo stesso valore,
    coerente con quanto persistito su disco."""
    s = Settings(secret_key="", data_dir=tmp_path)
    k1 = s.resolved_secret()
    k2 = s.resolved_secret()
    assert k1 == k2 == (tmp_path / "secret.key").read_text().strip()


# =========================================================================== #
# DIFESA CHE REGGE — SPA catch-all: path traversal NON legge fuori da dist
#   main.py:86-101 (resolve() + prefix check). Regression guard.
# =========================================================================== #
@pytest.mark.parametrize("path", [
    "/../../../../etc/passwd",
    "/..%2f..%2f..%2fetc%2fpasswd",
    "/%2e%2e/%2e%2e/etc/passwd",
    "/....//....//etc/passwd",
])
def test_safe_spa_path_traversal_is_blocked(path):
    """DIFESA OK: la guardia anti-traversal (resolve() + startswith(dist)) impedisce
    di servire file fuori dalla dist. Le richieste ostili ricadono su index.html
    (SPA fallback), MAI sul contenuto di /etc/passwd."""
    r = client.get(path)
    assert r.status_code == 200
    assert "root:x:0:0:" not in r.text  # nessun leak di /etc/passwd
