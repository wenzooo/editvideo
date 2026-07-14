"""Helper di resilienza puri e testabili: retry con backoff e circuit breaker.

Niente I/O reale qui dentro: `retry_call` accetta un `sleeper` iniettabile
(default ``time.sleep``) e `CircuitBreaker` un `clock` iniettabile (default
``time.monotonic``), cosi' i test verificano la logica senza attese vere.

Scelte KISS: un solo file, nessuna dipendenza esterna, adatto a un'app
single-node. Non serve una libreria di resilienza (tenacity, ecc.): sarebbe
sovradimensionata per i due casi d'uso reali (retry di un job, degrado del
modello Whisper).
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


def retry_call(
    fn: Callable[[], T],
    attempts: int,
    backoff: float,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    """Esegue ``fn()`` riprovando fino a ``attempts`` tentativi TOTALI.

    Backoff ESPONENZIALE: tra il tentativo ``i`` (0-based) e il successivo si
    attende ``backoff * 2**i`` secondi (0 = nessuna attesa). Solo le eccezioni
    elencate in ``retry_on`` vengono riprovate: qualsiasi altra risale subito
    (fail-fast sugli errori non transitori). Dopo l'ultimo tentativo fallito
    l'eccezione originale viene rilanciata. ``sleeper`` e' iniettabile: i test
    passano un finto per non attendere davvero.
    """
    attempts = max(1, attempts)
    last_exc: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except retry_on as exc:  # noqa: PERF203
            last_exc = exc
            if i + 1 >= attempts:
                break
            if backoff > 0:
                sleeper(backoff * (2 ** i))
    assert last_exc is not None  # per il type checker: il loop gira >=1 volta
    raise last_exc


class CircuitBreaker:
    """Circuit breaker minimale a soglia di fallimenti CONSECUTIVI.

    Dopo ``threshold`` fallimenti consecutivi il circuito si APRE per
    ``cooldown`` secondi: durante l'apertura ``allow()`` ritorna False, cosi'
    il chiamante degrada subito invece di ritentare un'operazione che sta gia'
    fallendo di continuo (es. il caricamento del modello Whisper principale sul
    disco effimero di HF). Un successo azzera il conteggio e richiude il
    circuito. Scaduto il cooldown si concede un tentativo (half-open).

    ``clock`` e' iniettabile (default ``time.monotonic``) per i test.
    """

    def __init__(
        self,
        threshold: int,
        cooldown: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = max(1, threshold)
        self.cooldown = cooldown
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    def allow(self) -> bool:
        """True se l'operazione puo' procedere. Se il circuito e' aperto ma il
        cooldown e' scaduto, si richiude (half-open) e si concede un tentativo."""
        if self._opened_at is None:
            return True
        if self._clock() - self._opened_at >= self.cooldown:
            self.reset()
            return True
        return False

    def record_success(self) -> None:
        """Un successo azzera i fallimenti e richiude il circuito."""
        self.reset()

    def record_failure(self) -> None:
        """Registra un fallimento; raggiunta la soglia, apre il circuito."""
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = self._clock()

    def reset(self) -> None:
        """Riporta il breaker allo stato iniziale (chiuso, zero fallimenti)."""
        self._failures = 0
        self._opened_at = None
