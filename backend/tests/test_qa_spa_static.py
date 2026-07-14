"""QA: SPA statica, asset buildati, security/cache headers (backend/app/main.py).

Copre via TestClient, tutto OFFLINE e senza DB:
- route catch-all spa(): serve index.html su "/" e fa fallback per le rotte
  client-side (es. /dashboard) che non esistono su disco;
- guardie del catch-all: /api/* e /assets/* inesistenti restano 404 (JSON per
  le API, mai l'HTML della SPA);
- anti path-traversal: nessuna variante (../, %2f, %2e%2e) restituisce mai il
  contenuto di file fuori da frontend/dist;
- middleware cache_control_assets: "immutable" SOLO su /assets/*;
- middleware security_headers + apply_security_headers (a livello di funzione:
  setdefault non sovrascrive header preesistenti);
- /api/health senza auth NON espone APP_VERSION (anti-fingerprinting,
  SECURITY_REPORT #8): il payload diagnostico completo richiede un token
  valido ed e' coperto in test_health_endpoint.py.

La build frontend/dist esiste nel repo (index.html + assets/): nessun mock.
Nessuna riga a DB, nessun login (nemmeno fallito): niente teardown necessario.
Preambolo env identico agli altri moduli (setdefault: in suite completa
vincono le env del primo modulo importato, a runtime si usa get_settings()).
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_qa_spa_static_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.responses import Response  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.security import SECURITY_HEADERS, apply_security_headers  # noqa: E402
from app.version import APP_VERSION  # noqa: E402

client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker

# Marker che NON devono mai comparire in una risposta della SPA: contenuti di
# backend/app/main.py e di /etc/passwd (obiettivi tipici del path traversal).
_LEAK_MARKERS = (b"asynccontextmanager", b"from fastapi import FastAPI", b"root:x:")


def _dist() -> Path:
    # sempre da get_settings(): in suite completa la config e' quella del primo
    # modulo importato, ma frontend_dist non e' mai impostata via env nei test,
    # quindi risolve comunque a <repo>/frontend/dist.
    return get_settings().resolved_frontend_dist()


def _index_bytes() -> bytes:
    return (_dist() / "index.html").read_bytes()


# --------------------------------------------------------------------------- #
# 1. index e fallback SPA
# --------------------------------------------------------------------------- #
def test_root_serves_index_html():
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.content == _index_bytes()
    assert b'<div id="root">' in r.content  # e' proprio la shell della SPA


def test_spa_client_route_falls_back_to_index():
    # /dashboard non esiste su disco dentro dist: la route catch-all deve
    # servire index.html (il router client-side gestira' il path).
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.content == _index_bytes()


def test_real_static_file_served_directly():
    # un file reale della dist (index.html richiesto per nome) viene servito
    # dal ramo `candidate.is_file()`, non dal fallback.
    r = client.get("/index.html")
    assert r.status_code == 200
    assert r.content == _index_bytes()


# --------------------------------------------------------------------------- #
# 2. guardie del catch-all: /api/* e /assets/* non diventano mai HTML
# --------------------------------------------------------------------------- #
def test_unknown_api_path_is_404_json_not_html():
    r = client.get("/api/inesistente")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": "Not Found"}
    assert b"<html" not in r.content.lower()  # mai il fallback SPA sulle API


def test_unknown_asset_is_404():
    r = client.get("/assets/inesistente.js")
    assert r.status_code == 404
    assert b'<div id="root">' not in r.content  # niente fallback a index.html


# --------------------------------------------------------------------------- #
# 3. anti path-traversal
# --------------------------------------------------------------------------- #
# Cosa arriva DAVVERO al server (verificato con httpx 0.28, che normalizza le
# URL lato client prima dell'invio):
#   "/../backend/app/main.py"   -> httpx rimuove i dot-segment: il server vede
#                                  "/backend/app/main.py" (path pulito) e la
#                                  spa() fa fallback a index.html;
#   "/..%2f..%2fetc%2fpasswd"   -> httpx NON decodifica %2f: il server riceve
#                                  il path percent-encoded, Starlette lo
#                                  decodifica in "../../etc/passwd" e la
#                                  guardia `inside` della spa() lo respinge
#                                  (resolve() esce dalla dist) -> index.html;
#   "/%2e%2e/%2e%2e/etc/passwd" -> idem: %2e resta encodato fino al server,
#                                  decodificato in "../../etc/passwd" e
#                                  respinto dalla guardia -> index.html.
# In tutti i casi il body NON deve mai contenere il contenuto del file preso
# di mira: o 404 o l'index.html della SPA.
@pytest.mark.parametrize("url", [
    "/../backend/app/main.py",
    "/..%2f..%2fetc%2fpasswd",
    "/%2e%2e/%2e%2e/etc/passwd",
])
def test_path_traversal_never_leaks_files(url):
    r = client.get(url)
    assert r.status_code in (200, 404)
    for marker in _LEAK_MARKERS:
        assert marker not in r.content, f"{url} ha esposto un file fuori da dist"
    if r.status_code == 200:
        # se risponde 200 deve essere il fallback a index.html, non altro
        assert r.headers["content-type"].startswith("text/html")
        assert r.content == _index_bytes()


def test_traversal_to_backend_source_never_serves_python():
    # variante non normalizzabile dal client: i ".." arrivano al server dentro
    # il path param (Starlette decodifica %2E in "."), la guardia li respinge.
    r = client.get("/..%2Fbackend%2Fapp%2Fmain.py")
    assert r.status_code in (200, 404)
    assert b"asynccontextmanager" not in r.content
    assert b"get_settings" not in r.content


# --------------------------------------------------------------------------- #
# 4. Cache-Control
# --------------------------------------------------------------------------- #
def test_real_asset_has_immutable_cache_control():
    assets = sorted((_dist() / "assets").glob("*.js"))
    assert assets, "frontend/dist/assets senza .js: build frontend mancante?"
    r = client.get(f"/assets/{assets[0].name}")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "immutable" in cc
    assert "max-age=31536000" in cc


def test_index_is_not_cached_immutable():
    # index.html NON ha hash nel nome: cache aggressiva romperebbe i deploy.
    r = client.get("/")
    assert r.status_code == 200
    assert "immutable" not in r.headers.get("cache-control", "")


# --------------------------------------------------------------------------- #
# 5. security headers
# --------------------------------------------------------------------------- #
def test_api_response_has_security_headers():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    # gli altri header del set devono esserci tutti (middleware su OGNI risposta)
    for key, value in SECURITY_HEADERS.items():
        assert r.headers.get(key) == value


def test_apply_security_headers_does_not_override_existing():
    # setdefault: un header gia' impostato dalla risposta (es. una Cache-Control
    # custom, o qui un nosniff "diverso") NON deve essere sovrascritto.
    resp = Response(content="x", headers={"X-Content-Type-Options": "valore-preesistente"})
    apply_security_headers(resp.headers)
    assert resp.headers["X-Content-Type-Options"] == "valore-preesistente"
    # ...mentre gli header mancanti vengono comunque aggiunti
    assert resp.headers["Referrer-Policy"] == SECURITY_HEADERS["Referrer-Policy"]


def test_apply_security_headers_sets_all_defaults_on_bare_response():
    resp = Response(content="x")
    apply_security_headers(resp.headers)
    for key, value in SECURITY_HEADERS.items():
        assert resp.headers[key] == value


# --------------------------------------------------------------------------- #
# 6. /api/health senza auth non espone la versione
# --------------------------------------------------------------------------- #
def test_health_without_auth_hides_app_version():
    # anti-fingerprinting (SECURITY_REPORT #8): niente APP_VERSION agli anonimi;
    # il payload completo autenticato e' coperto in test_health_endpoint.py.
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert APP_VERSION not in r.text
