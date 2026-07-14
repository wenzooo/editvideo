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

from fastapi import HTTPException, Request

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
#   * `Permissions-Policy` MINIMALE: disattiva solo feature del browser che la
#     SPA non usa mai (geo/mic/camera). NON si tocca `autoplay` (servirebbe alla
#     riproduzione dei video) né si impostano COOP/COEP/CORP: quelli
#     ROMPEREBBERO l'embedding cross-site nell'iframe di HF (trade-off accettato).
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

    Due difese aggiuntive contro l'abuso di ``X-Forwarded-For`` spoofabile
    (SECURITY_REPORT #2):
    - ``global_max_attempts``: un contatore GLOBALE (tutti gli IP insieme). Anche
      ruotando l'header a ogni richiesta, i tentativi confluiscono qui: oltre la
      soglia scatta il 429 per tutti -> il bypass del rate-limit e' chiuso.
    - ``max_keys``: tetto al numero di chiavi tenute in memoria (evizione delle
      piu' vecchie) -> niente crescita illimitata del dict (DoS memoria).
    """

    def __init__(self, max_attempts: int, window_seconds: float,
                 global_max_attempts: int = 0, max_keys: int = 0):
        self.max_attempts = max(1, int(max_attempts))
        self.window_seconds = max(1.0, float(window_seconds))
        self.global_max_attempts = max(0, int(global_max_attempts))
        self.max_keys = max(0, int(max_keys))
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()
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

    def _purge_global(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._global and self._global[0] <= cutoff:
            self._global.popleft()

    def _evict_if_needed(self) -> None:
        # rimuove le chiavi piu' vecchie (ordine d'inserimento del dict) oltre il tetto
        if self.max_keys:
            while len(self._hits) > self.max_keys:
                oldest = next(iter(self._hits))
                self._hits.pop(oldest, None)

    def is_blocked(self, key: str, now: float | None = None) -> bool:
        """True se la chiave (o il contatore globale) ha raggiunto la soglia."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._purge(key, now)
            self._purge_global(now)
            dq = self._hits.get(key)
            if dq is not None and len(dq) >= self.max_attempts:
                return True
            return bool(self.global_max_attempts
                        and len(self._global) >= self.global_max_attempts)

    def record(self, key: str, now: float | None = None) -> None:
        """Registra un tentativo fallito per la chiave (e nel contatore globale)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._purge(key, now)
            self._purge_global(now)
            self._hits[key].append(now)
            if self.global_max_attempts:
                self._global.append(now)
            self._evict_if_needed()

    def reset(self, key: str) -> None:
        """Azzera la chiave (chiamato dopo un login riuscito). Il contatore globale
        NON viene azzerato: e' un freno anti brute-force distribuito, indipendente
        dal singolo successo."""
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        """Svuota completamente lo stato (utile nei test)."""
        with self._lock:
            self._hits.clear()
            self._global.clear()

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Secondi (>=1) stimati prima che si liberi almeno uno slot."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._purge(key, now)
            self._purge_global(now)
            fronts: list[float] = []
            dq = self._hits.get(key)
            if dq and len(dq) >= self.max_attempts:
                fronts.append(dq[0])
            if self.global_max_attempts and len(self._global) >= self.global_max_attempts:
                fronts.append(self._global[0])
            if not fronts:
                return 0
            return max(1, int(self.window_seconds - (now - min(fronts))) + 1)


@lru_cache
def get_login_rate_limiter() -> RateLimiter:
    """Singleton del rate limiter del login, con soglie da configurazione."""
    from .config import get_settings

    s = get_settings()
    return RateLimiter(s.login_max_attempts, s.login_window_seconds,
                       global_max_attempts=s.login_global_max_attempts,
                       max_keys=s.rate_limit_max_keys)


@lru_cache
def get_write_rate_limiter() -> RateLimiter:
    """Singleton del rate limiter GENERICO per gli endpoint mutanti pesanti
    (upload / enqueue di massa / export).

    Riusa la stessa classe :class:`RateLimiter` del login (DRY) ma con una
    filosofia d'uso diversa: qui si conta OGNI richiesta accettata (non solo i
    fallimenti) e non c'è ``reset``, perché non c'è un "successo" che azzeri il
    conteggio -- è un semplice tetto di richieste per IP nella finestra. Le
    soglie arrivano da ``upload_rate_max`` / ``upload_rate_window_seconds``.
    ``max_keys`` è condiviso col login (stesso tetto anti DoS di memoria)."""
    from .config import get_settings

    s = get_settings()
    return RateLimiter(s.upload_rate_max, s.upload_rate_window_seconds,
                       max_keys=s.rate_limit_max_keys)


def rate_limit_writes(request: Request) -> None:
    """Dependency FastAPI riutilizzabile per frenare gli endpoint mutanti pesanti.

    Applicabile a upload / enqueue di massa / export come
    ``Depends(rate_limit_writes)`` (o a livello di router). Difesa in profondità
    contro il flood di job da parte di un client (anche autenticato) impazzito:
    oltre ``upload_rate_max`` richieste per IP nella finestra scorrevole risponde
    429 con ``Retry-After``. Il conteggio è per-IP (via :func:`client_ip`), con lo
    stesso tetto di chiavi del login per evitare la crescita illimitata in memoria.

    Nota: va montata DOPO ``require_auth`` (che così respinge le anonime con 401
    senza consumare il budget del limiter — least privilege)."""
    limiter = get_write_rate_limiter()
    ip = client_ip(request)
    if limiter.is_blocked(ip):
        raise HTTPException(
            status_code=429,
            detail="Troppe richieste: rallenta e riprova tra poco.",
            headers={"Retry-After": str(limiter.retry_after(ip))},
        )
    limiter.record(ip)
