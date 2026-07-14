"""Misure di sicurezza aggiuntive, retro-compatibili con l'embedding in iframe.

Contiene, senza dipendenze esterne:

- ``RateLimiter``: rate limiter sliding-window in-memory e thread-safe, usato per
  frenare i tentativi di login a forza bruta *per IP*;
- ``client_ip``: estrazione dell'IP client robusta dietro il reverse-proxy di
  Hugging Face (X-Forwarded-For / X-Real-IP), con fallback al peer del socket;
- ``SECURITY_HEADERS``: header di sicurezza applicati a TUTTE le risposte,
  scelti apposta per NON rompere l'esecuzione dentro un iframe cross-site
  (nessun ``X-Frame-Options``, nessuna ``frame-ancestors`` restrittiva).

Nessun segreto transita da qui: il rate limiter memorizza solo IP + timestamp,
mai password o token.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from functools import lru_cache

from fastapi import Request

# --------------------------------------------------------------------------- #
# Security headers (compatibili con l'iframe di Hugging Face)
# --------------------------------------------------------------------------- #
# Scelte deliberate per restare embeddabili cross-site:
#   * NIENTE X-Frame-Options (DENY/SAMEORIGIN romperebbe l'iframe);
#   * CSP con `frame-ancestors *` (permissiva): NON limita chi può inquadrare
#     l'app, quindi l'embedding di HF continua a funzionare. La CSP restringe
#     solo vettori che la SPA non usa (`<object>/<embed>` e `<base>` cross-origin),
#     senza toccare script/style/img/connect/font/media: così non rompe il
#     frontend buildato né lo streaming dei media.
#   * `nosniff` è sicuro perché i Content-Type sono sempre impostati corretti.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": "frame-ancestors *; object-src 'none'; base-uri 'self'",
}


def apply_security_headers(headers) -> None:
    """Applica gli header di sicurezza a una risposta senza sovrascrivere quelli
    già presenti (es. Cache-Control impostato altrove)."""
    for key, value in SECURITY_HEADERS.items():
        headers.setdefault(key, value)


# --------------------------------------------------------------------------- #
# Estrazione IP client (dietro reverse-proxy)
# --------------------------------------------------------------------------- #
def client_ip(request: Request) -> str:
    """IP del client per il rate limit.

    Dietro il proxy di Hugging Face il socket peer è il proxy, non il client:
    per non far condividere lo stesso "secchiello" a tutti gli utenti (che
    penalizzerebbe quelli legittimi durante un attacco) si preferisce l'IP reale
    portato da ``X-Forwarded-For`` / ``X-Real-IP``. Nota: questi header sono
    spoofabili in assenza di una whitelist di proxy fidati; qui il rate limit è
    una difesa in profondità (l'auth resta HMAC + password), quindi il trade-off
    a favore del non-penalizzare gli utenti legittimi è accettabile.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    xri = request.headers.get("x-real-ip")
    if xri and xri.strip():
        return xri.strip()
    client = request.client
    return client.host if client else "unknown"


# --------------------------------------------------------------------------- #
# Rate limiter sliding-window
# --------------------------------------------------------------------------- #
class RateLimiter:
    """Rate limiter a finestra scorrevole, in-memory e thread-safe.

    Per ogni chiave (IP) tiene una coda dei timestamp dei tentativi "contati".
    L'app conta SOLO i login falliti e chiama :meth:`reset` sul successo, così
    un utente legittimo che indovina la password non viene mai bloccato.
    """

    def __init__(self, max_attempts: int, window_seconds: float):
        self.max_attempts = max(1, int(max_attempts))
        self.window_seconds = max(1.0, float(window_seconds))
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _purge(self, key: str, now: float) -> None:
        dq = self._hits.get(key)
        if dq is None:
            return
        cutoff = now - self.window_seconds
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if not dq:
            self._hits.pop(key, None)

    def is_blocked(self, key: str, now: float | None = None) -> bool:
        """True se la chiave ha raggiunto la soglia nella finestra corrente."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._purge(key, now)
            dq = self._hits.get(key)
            return dq is not None and len(dq) >= self.max_attempts

    def record(self, key: str, now: float | None = None) -> None:
        """Registra un tentativo fallito per la chiave."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._purge(key, now)
            self._hits[key].append(now)

    def reset(self, key: str) -> None:
        """Azzera la chiave (chiamato dopo un login riuscito)."""
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        """Svuota completamente lo stato (utile nei test)."""
        with self._lock:
            self._hits.clear()

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Secondi (>=1) stimati prima che si liberi almeno uno slot."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._purge(key, now)
            dq = self._hits.get(key)
            if not dq:
                return 0
            return max(1, int(self.window_seconds - (now - dq[0])) + 1)


@lru_cache
def get_login_rate_limiter() -> RateLimiter:
    """Singleton del rate limiter del login, con soglie da configurazione."""
    from .config import get_settings

    s = get_settings()
    return RateLimiter(s.login_max_attempts, s.login_window_seconds)
