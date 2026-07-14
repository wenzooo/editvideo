from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from os import sep as os_sep
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .config import get_settings
from .db import init_db
from .logging_conf import setup_logging
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
async def security_headers(request: Request, call_next):
    """Header di sicurezza su TUTTE le risposte, compatibili con l'iframe HF.

    Niente X-Frame-Options e CSP con `frame-ancestors *`: l'embedding cross-site
    resta possibile. `setdefault` non sovrascrive header già impostati (es. la
    Cache-Control degli asset qui sopra o delle thumbnail)."""
    response = await call_next(request)
    apply_security_headers(response.headers)
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
