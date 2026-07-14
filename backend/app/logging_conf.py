"""Setup centralizzato del logging strutturato.

Un solo handler sullo stream standard, formato con timestamp/livello/logger e,
quando disponibili, gli identificativi di job/video correnti. Gli id vengono
iniettati via `contextvars`, quindi restano corretti anche con più worker
thread in parallelo (ogni thread ha il suo contesto isolato).

Livello da env `LOG_LEVEL` (default INFO). Nessun segreto viene mai loggato:
la configurazione "effettiva" mostrata all'avvio passa da Settings.public_config().
"""
from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager
from typing import Iterator

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s%(ctx)s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Contesto per-thread/async: id di correlazione della richiesta HTTP e id del
# job/video in lavorazione. Il request-id e' impostato dal middleware in main.py
# (uno per richiesta), job/video dal worker: cosi' ogni riga di log e' tracciabile.
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_job_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("job_id", default=None)
_video_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("video_id", default=None)

_configured = False


def _short(value: str | None) -> str | None:
    """Accorcia gli id a 8 caratteri per log leggibili (come nel resto del worker)."""
    if not value:
        return None
    v = str(value)
    return v[:8] if len(v) > 8 else v


class _ContextFilter(logging.Filter):
    """Inietta job_id/video_id correnti in ogni record: prepara il campo `ctx`
    pronto per il formatter (stringa vuota quando non c'è nessun job attivo)."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = _request_id_var.get()
        job_id = _job_id_var.get()
        video_id = _video_id_var.get()
        record.request_id = request_id or "-"
        record.job_id = job_id or "-"
        record.video_id = video_id or "-"
        bits = []
        if request_id:
            bits.append(f"req={request_id}")
        if job_id:
            bits.append(f"job={job_id}")
        if video_id:
            bits.append(f"video={video_id}")
        record.ctx = (" [" + " ".join(bits) + "]") if bits else ""
        return True


def _resolve_level(level: str | int | None) -> int:
    """Risolve il livello: argomento esplicito -> env LOG_LEVEL -> Settings -> INFO.
    Non solleva mai: in caso di dubbio ripiega su INFO."""
    if level is None:
        level = os.environ.get("LOG_LEVEL")
    if level is None:
        try:
            from .config import get_settings
            level = get_settings().log_level
        except Exception:
            level = "INFO"
    if isinstance(level, int):
        return level
    return getattr(logging, str(level).strip().upper(), logging.INFO)


def setup_logging(level: str | int | None = None, force: bool = False) -> None:
    """Configura il root logger in modo idempotente e sicuro da chiamare più volte.

    Sostituisce eventuali handler già presenti con uno solo dal formato strutturato,
    così l'output resta pulito anche se qualcuno aveva già chiamato basicConfig().
    """
    global _configured
    lvl = _resolve_level(level)
    root = logging.getLogger()
    if _configured and not force:
        root.setLevel(lvl)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    handler.addFilter(_ContextFilter())
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(lvl)
    _configured = True


@contextmanager
def log_context(job_id: str | None = None, video_id: str | None = None,
                request_id: str | None = None) -> Iterator[None]:
    """Associa request_id/job_id/video_id a tutti i log emessi nel blocco.

    Basato su contextvars: thread-safe e ripristinato all'uscita (anche su errore).
    """
    tokens = []
    if request_id is not None:
        tokens.append((_request_id_var, _request_id_var.set(_short(request_id))))
    if job_id is not None:
        tokens.append((_job_id_var, _job_id_var.set(_short(job_id))))
    if video_id is not None:
        tokens.append((_video_id_var, _video_id_var.set(_short(video_id))))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
