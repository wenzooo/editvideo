"""Feature flag centralizzati e minimali.

Un unico punto per accendere/spegnere funzionalita' a runtime, senza dipendenze
esterne. Gli interruttori vivono nella stringa di config ``feature_flags`` in
formato ``"nome=1,altro=0,terzo"``:

- ``nome=1`` / ``nome=true`` / ``nome=on`` / ``nome=yes`` -> acceso;
- ``nome=0`` (o qualsiasi altro valore) -> spento;
- ``nome`` (nome nudo, senza ``=``) -> acceso (scorciatoia);
- un nome sconosciuto (non presente nella stringa) -> spento.

Il parsing (``parse_flags``) e' una funzione pura testabile senza env; i wrapper
``feature_flags`` / ``is_enabled`` leggono la config.
"""
from __future__ import annotations

from ..config import get_settings

# valori testuali interpretati come "vero" (case-insensitive)
_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: str) -> bool:
    """Interpreta il lato destro di ``nome=valore`` come booleano."""
    return value.strip().lower() in _TRUTHY


def parse_flags(raw: str) -> dict[str, bool]:
    """Interpreta una stringa ``"a=1,b=0,c"`` in un dizionario ``nome -> bool``.

    Funzione pura: nessuna lettura di env/config. Token vuoti e nomi vuoti sono
    ignorati; in caso di nome duplicato vince l'ultima occorrenza.
    """
    flags: dict[str, bool] = {}
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        if "=" in token:
            name, _, value = token.partition("=")
            name = name.strip()
            if not name:
                continue
            flags[name] = _truthy(value)
        else:
            # nome nudo senza "=" -> abilitato
            flags[token] = True
    return flags


def feature_flags() -> dict[str, bool]:
    """Dizionario ``nome -> bool`` dei flag effettivi, letti dalla config."""
    return parse_flags(get_settings().feature_flags)


def is_enabled(name: str) -> bool:
    """True se il flag ``name`` e' esplicitamente acceso, altrimenti False.

    Un nome sconosciuto o spento ricade sempre su False (default sicuro).
    """
    return feature_flags().get(name, False)
