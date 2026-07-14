"""Test dei limiti d'upload: tetto su Content-Length e sul numero di file.

Copre il middleware `request_size_guard` (main.py) e il controllo
`max_upload_files` in cima all'endpoint upload (routers/videos.py):
- Content-Length oltre `resolved_max_request_bytes()` -> 413 SENZA eseguire
  l'endpoint (il body non deve mai essere letto/spolato da Starlette);
- il tetto auto (MAX_REQUEST_MB=0) fa spazio a max_upload_files file al
  limite: una richiesta multi-file lecita NON viene respinta;
- Content-Length assente (upload chunked) -> 411;
- le risposte anticipate 411/413 del middleware portano comunque gli header di
  sicurezza (security_headers resta il middleware piu' esterno, come da
  docstring di upload_request_size_guard in main.py);
- route NON di upload con body dichiarato oltre max_json_body_kb -> 413
  (anti DoS di memoria: FastAPI bufferizza il body in RAM prima di validare);
- troppe parti file -> 413 prima di processare qualsiasi file;
- l'upload normale piccolo continua a funzionare (ffmpeg monkeypatchato come
  in test_qa_upload_api.py: mai eseguito davvero).

TestClient/httpx normalizzano il Content-Length in base al body reale, quindi
i casi "header falso/assente" si esercitano invocando l'app direttamente a
livello ASGI con header costruiti a mano (via robusta, nessuna dipendenza dai
dettagli di merge-header di httpx).

Ambiente isolato configurato PRIMA di importare l'app (get_settings() e'
@lru_cache): stesso preambolo dei moduli esistenti, setdefault non sovrascrive
nulla di gia' impostato — a runtime si usano SEMPRE i valori di get_settings().
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_upload_limits_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

import app.routers.videos as videos_router  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Video  # noqa: E402
from app.security import SECURITY_HEADERS, get_login_rate_limiter  # noqa: E402

init_db()
get_settings().ensure_dirs()
client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker

FAKE_META = {"duration": 12.0, "width": 1920, "height": 1080, "fps": 30.0, "has_audio": True}

UPLOAD_PATH = "/api/videos/upload"


# --------------------------------------------------------------------------- #
# helper
# --------------------------------------------------------------------------- #
def _auth() -> dict:
    # solo login riusciti: il clear evita che eventuali 429 lasciati da altri
    # moduli blocchino questo login, e non lascia residui.
    get_login_rate_limiter().clear()
    r = client.post("/api/auth/login", json={"password": get_settings().admin_password},
                    headers={"X-Forwarded-For": "8.8.4.4"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _auth_asgi() -> list[tuple[bytes, bytes]]:
    """Header Authorization di _auth() in formato ASGI (coppie di byte), per
    _asgi_post: rende le richieste respinte AUTENTICATE, cosi' che senza la
    guardia arriverebbero davvero all'endpoint (e non morirebbero in un 401)."""
    return [(k.lower().encode(), v.encode()) for k, v in _auth().items()]


def _video_count() -> int:
    with SessionLocal() as db:
        return len(db.execute(select(Video.id)).all())


def _assert_security_headers(resp_headers: dict) -> None:
    """Gli header di sicurezza devono esserci anche sulle risposte anticipate
    del middleware (pinna l'ordine dei middleware dichiarato in main.py)."""
    for key, value in SECURITY_HEADERS.items():
        assert resp_headers.get(key.lower()) == value


def _asgi_post(path: str, headers: list[tuple[bytes, bytes]],
               body: bytes = b"") -> tuple[int, dict, dict]:
    """POST invocando l'app direttamente come ASGI, con controllo TOTALE sugli
    header (Content-Length falso, assente o malformato). Ritorna
    (status, json, header di risposta con chiavi minuscole).
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    sent_body = {"done": False}

    async def receive():
        if not sent_body["done"]:
            sent_body["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    messages: list[dict] = []

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    resp_headers = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    payload = json.loads(raw) if raw else {}
    return start["status"], payload, resp_headers


@pytest.fixture(autouse=True)
def _db_cleanup():
    """Snapshot dei video esistenti; a fine test si eliminano SOLO le righe
    nuove e i loro file (in suite completa il DB e' condiviso tra moduli)."""
    with SessionLocal() as db:
        before = {vid for (vid,) in db.execute(select(Video.id))}
    yield
    with SessionLocal() as db:
        for (vid,) in db.execute(select(Video.id)):
            if vid in before:
                continue
            v = db.get(Video, vid)
            for p in (v.stored_path, v.thumbnail_path, v.exported_path):
                if p:
                    Path(p).unlink(missing_ok=True)
            db.delete(v)  # cascade ORM: elimina anche job e segmenti collegati
        db.commit()


@pytest.fixture
def probe_recorder(monkeypatch):
    """Upload senza ffmpeg reale + traccia delle chiamate a probe: serve sia a
    far passare gli upload leciti sia a dimostrare che nei casi respinti
    l'endpoint non arriva MAI a processare i file (probe mai invocato)."""
    calls: list = []

    def fake_probe(path):
        calls.append(str(path))
        return dict(FAKE_META)

    def fake_thumb(src, dst, at=1.0):
        Path(dst).write_bytes(b"jpg")

    monkeypatch.setattr(videos_router.ff, "probe", fake_probe)
    monkeypatch.setattr(videos_router.ff, "make_thumbnail", fake_thumb)
    return calls


# --------------------------------------------------------------------------- #
# 1. Content-Length oltre soglia -> 413, endpoint mai eseguito
# --------------------------------------------------------------------------- #
def test_oversized_content_length_413_endpoint_never_runs(probe_recorder):
    n_before = _video_count()
    too_big = get_settings().resolved_max_request_bytes() + 1
    # richiesta AUTENTICATA: senza la guardia arriverebbe davvero all'endpoint,
    # quindi probe_recorder e il conteggio righe sono asserzioni discriminanti
    status, payload, resp_headers = _asgi_post(UPLOAD_PATH, headers=[
        *_auth_asgi(),
        (b"content-length", str(too_big).encode()),
        (b"content-type", b"multipart/form-data; boundary=xyz"),
    ])
    assert status == 413
    assert "detail" in payload and "MB" in payload["detail"]
    # il middleware risponde PRIMA di call_next: niente parsing multipart,
    # niente probe, nessuna riga a DB
    assert probe_recorder == []
    assert _video_count() == n_before
    # anche il 413 anticipato porta gli header di sicurezza
    _assert_security_headers(resp_headers)


def test_content_length_at_limit_passes_middleware(probe_recorder):
    # Content-Length esattamente al tetto: il middleware NON deve respingere
    # (il body reale qui e' vuoto/minuscolo: si verifica solo che la richiesta
    # arrivi oltre la guardia, cioe' che la risposta non sia 411/413).
    at_limit = get_settings().resolved_max_request_bytes()
    status, _, _ = _asgi_post(UPLOAD_PATH, headers=[
        (b"content-length", str(at_limit).encode()),
        (b"content-type", b"multipart/form-data; boundary=xyz"),
    ])
    assert status not in (411, 413)


def test_multi_file_upload_within_per_file_limits_passes_middleware(probe_recorder):
    # Regressione: 2 file da 1.5 GB (ognuno < max_upload_mb, count <= max_upload_files)
    # dichiarano ~3 GB totali. Col vecchio tetto auto (max_upload_mb + 64 MB)
    # venivano respinti con 413 nonostante fossero leciti per l'endpoint.
    declared = 2 * 1536 * 1024 * 1024  # ~3 GB
    assert declared <= get_settings().resolved_max_request_bytes()
    status, _, _ = _asgi_post(UPLOAD_PATH, headers=[
        (b"content-length", str(declared).encode()),
        (b"content-type", b"multipart/form-data; boundary=xyz"),
    ])
    assert status not in (411, 413)


def test_auto_request_cap_fits_max_files():
    # Tetto auto (MAX_REQUEST_MB=0): deve contenere max_upload_files file al
    # limite max_upload_mb, piu' il margine per l'overhead multipart.
    s = Settings(max_request_mb=0, max_upload_mb=2048, max_upload_files=10)
    assert s.resolved_max_request_bytes() >= 10 * 2048 * 1024 * 1024
    # un valore esplicito vince sempre sull'auto
    s = Settings(max_request_mb=100, max_upload_mb=2048, max_upload_files=10)
    assert s.resolved_max_request_bytes() == 100 * 1024 * 1024


# --------------------------------------------------------------------------- #
# 2. Content-Length assente o malformato -> 411
# --------------------------------------------------------------------------- #
def test_missing_content_length_411(probe_recorder):
    n_before = _video_count()
    # autenticata: vedi test_oversized_content_length_413_endpoint_never_runs
    status, payload, resp_headers = _asgi_post(UPLOAD_PATH, headers=[
        *_auth_asgi(),
        (b"content-type", b"multipart/form-data; boundary=xyz"),
        (b"transfer-encoding", b"chunked"),
    ])
    assert status == 411
    assert "detail" in payload and "Content-Length" in payload["detail"]
    assert probe_recorder == []
    assert _video_count() == n_before
    # anche il 411 anticipato porta gli header di sicurezza
    _assert_security_headers(resp_headers)


def test_malformed_content_length_411(probe_recorder):
    # autenticata: vedi test_oversized_content_length_413_endpoint_never_runs
    status, payload, _ = _asgi_post(UPLOAD_PATH, headers=[
        *_auth_asgi(),
        (b"content-length", b"non-un-numero"),
        (b"content-type", b"multipart/form-data; boundary=xyz"),
    ])
    assert status == 411
    assert "detail" in payload
    assert probe_recorder == []


# --------------------------------------------------------------------------- #
# 3. route NON di upload: tetto piccolo (max_json_body_kb) sul body dichiarato
# --------------------------------------------------------------------------- #
def test_non_upload_routes_capped_at_json_limit():
    # POST anonimo su /api/auth/login con Content-Length enorme: respinto con
    # 413 dal middleware PRIMA che il body venga bufferizzato in RAM (anti DoS)
    huge = get_settings().max_json_body_kb * 1024 + 1
    status, payload, _ = _asgi_post("/api/auth/login", headers=[
        (b"content-length", str(huge).encode()),
        (b"content-type", b"application/json"),
    ])
    assert status == 413
    assert "detail" in payload and "KB" in payload["detail"]


def test_non_upload_routes_small_body_passes():
    # GET senza Content-Length: nessun 411/413
    r = client.get("/api/health")
    assert r.status_code == 200
    # un login normale (body piccolo) attraversa la guardia e funziona
    assert "Authorization" in _auth()


# --------------------------------------------------------------------------- #
# 4. troppe parti file -> 413 prima di processare qualsiasi file
# --------------------------------------------------------------------------- #
def test_too_many_files_413_nothing_processed(probe_recorder):
    n_before = _video_count()
    n_files = get_settings().max_upload_files + 1
    files = [("files", (f"clip{i}.mp4", b"finto-mp4", "video/mp4")) for i in range(n_files)]
    r = client.post(UPLOAD_PATH, files=files, headers=_auth())
    assert r.status_code == 413
    assert str(get_settings().max_upload_files) in r.json()["detail"]
    # respinto in cima all'endpoint: nessun file processato, nessuna riga a DB
    assert probe_recorder == []
    assert _video_count() == n_before


def test_max_files_exactly_at_limit_ok(probe_recorder):
    n_files = get_settings().max_upload_files
    files = [("files", (f"ok{i}.mp4", b"finto-mp4", "video/mp4")) for i in range(n_files)]
    r = client.post(UPLOAD_PATH, files=files, headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert len(body["created"]) == n_files


# --------------------------------------------------------------------------- #
# 5. l'upload normale piccolo continua a funzionare
# --------------------------------------------------------------------------- #
def test_small_upload_still_works(probe_recorder):
    r = client.post(UPLOAD_PATH,
                    files=[("files", ("piccolo.mp4", b"finto-mp4", "video/mp4"))],
                    headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert len(body["created"]) == 1
    assert body["created"][0]["original_name"] == "piccolo.mp4"
    assert probe_recorder != []  # stavolta l'endpoint ha processato il file
