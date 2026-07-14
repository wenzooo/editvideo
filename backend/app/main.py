from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from os import sep as os_sep
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .config import get_settings
from .db import init_db
from .logging_conf import log_context, setup_logging
from .routers import batch, jobs, subtitles, templates, videos
from .security import apply_security_headers
from .version import APP_VERSION

setup_logging()
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    setup_logging()  # idempotente: garantisce il formato strutturato anche sotto uvicorn
    log.info("EditVideo %s in avvio — config effettiva: %s", APP_VERSION, settings.public_config())
    for warn in settings.validate_runtime():
        log.warning("Config sospetta: %s", warn)
    init_db()
    stop_event = None
    if settings.embedded_worker:
        from .worker import start_embedded_worker
        stop_event = start_embedded_worker()
        log.info("Worker embedded avviato")
    if settings.admin_password == "changeme":
        # In produzione la password di default NON è accettabile: meglio non
        # partire che esporre l'app forgiabile (SECURITY_REPORT #6).
        if settings.app_env == "prod":
            raise RuntimeError(
                "ADMIN_PASSWORD è ancora 'changeme' e APP_ENV=prod: imposta una "
                "password vera (env ADMIN_PASSWORD) prima di avviare l'app.")
        log.warning("ADMIN_PASSWORD è ancora 'changeme': cambiala prima di esporre l'app!")
    yield
    if stop_event:
        stop_event.set()


app = FastAPI(title="EditVideo", version=APP_VERSION, lifespan=lifespan)

# Compressione gzip delle risposte grandi (es. lista video con sottotitoli)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def cache_control_assets(request: Request, call_next):
    """Cache aggressiva per gli asset buildati: hanno l'hash nel nome, quindi immutabili."""
    response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.middleware("http")
async def request_size_guard(request: Request, call_next):
    """Guardia sulla dimensione delle richieste PRIMA che il body venga letto
    (SECURITY_REPORT #4).

    Upload (POST /api/videos/upload): quando l'handler arriva a girare,
    Starlette ha GIA' spolato l'intero body multipart su file temporaneo: il
    controllo per-file su max_upload_mb dentro l'endpoint quindi NON protegge
    il disco. Qui si valida il Content-Length dichiarato e, se la richiesta
    e' fuori misura, si risponde subito SENZA chiamare call_next: il body non
    viene mai letto ne' scritto su disco. Content-Length assente (es.
    Transfer-Encoding: chunked) -> 411, perche' senza dimensione dichiarata
    il tetto non e' verificabile in anticipo.

    Tutte le ALTRE route con body dichiarato hanno un tetto molto piu'
    piccolo (max_json_body_kb): FastAPI le legge con `await request.body()`
    accumulando i chunk in RAM PRIMA della validazione, e /api/auth/login non
    e' nemmeno autenticata -> senza tetto un Content-Length enorme sarebbe un
    DoS di memoria senza credenziali.

    NB: definito PRIMA di security_headers, che cosi' resta il middleware
    piu' esterno e applica gli header anche a questi 411/413 anticipati.
    """
    is_upload = request.method == "POST" and request.url.path == "/api/videos/upload"
    content_length = request.headers.get("content-length")
    if is_upload and content_length is None:
        return JSONResponse(
            {"detail": "Content-Length obbligatorio per l'upload "
                       "(upload chunked non supportato)"},
            status_code=411,
        )
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            return JSONResponse(
                {"detail": "Content-Length non valido"}, status_code=411)
        settings = get_settings()
        if is_upload:
            max_bytes = settings.resolved_max_request_bytes()
            limite = f"{max_bytes // (1024 * 1024)} MB per richiesta di upload"
        else:
            max_bytes = settings.max_json_body_kb * 1024
            limite = f"{settings.max_json_body_kb} KB per le richieste non di upload"
        if declared > max_bytes:
            return JSONResponse(
                {"detail": f"Richiesta troppo grande: massimo {limite}"},
                status_code=413,
            )
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Header di sicurezza su TUTTE le risposte, compatibili con l'iframe HF.

    Niente X-Frame-Options e CSP con `frame-ancestors *`: l'embedding cross-site
    resta possibile. `setdefault` non sovrascrive header già impostati (es. la
    Cache-Control degli asset qui sopra o delle thumbnail)."""
    response = await call_next(request)
    apply_security_headers(response.headers)
    return response


@app.middleware("http")
async def request_id(request: Request, call_next):
    """Assegna/propaga un Request ID di correlazione (tracing).

    Legge l'header configurato (`request_id_header`): se il client ne fornisce
    uno lo si rispetta, altrimenti se ne genera uno nuovo. L'id viene messo nel
    contesto di logging (`log_context`, riuso DRY del meccanismo a contextvars
    gia' usato per job/video) cosi' ogni riga emessa durante la richiesta e'
    correlabile, e viene rimandato al client nello stesso header di risposta.

    Definito DOPO security_headers: e' quindi il middleware piu' ESTERNO, cosi'
    l'header e il contesto coprono anche le risposte anticipate degli altri
    middleware (es. i 411/413 della guardia sulla dimensione)."""
    header = get_settings().request_id_header or "X-Request-ID"
    rid = request.headers.get(header) or uuid4().hex
    with log_context(request_id=rid):
        response = await call_next(request)
    response.headers[header] = rid
    return response


app.include_router(auth.router)
app.include_router(videos.router)
app.include_router(subtitles.router)
app.include_router(batch.router)
app.include_router(templates.router)
app.include_router(jobs.router)

# ---- SPA statica (frontend/dist), se buildata ----
_dist = get_settings().resolved_frontend_dist()
if (_dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith(("api/", "assets/")):
        raise HTTPException(404)
    candidate = (_dist / full_path).resolve()
    # anti path-traversal: si servono SOLO file dentro la dist
    inside = str(candidate).startswith(str(_dist.resolve()) + os_sep) or candidate == _dist.resolve()
    if full_path and inside and candidate.is_file():
        return FileResponse(candidate)
    index = _dist / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(
        404,
        "Frontend non buildato: esegui `cd frontend && npm install && npm run build`",
    )
