"""Isolamento dello stato globale di autenticazione tra i test.

Il rate limiter del login e' un singleton (lru_cache) con una finestra molto piu'
lunga della durata dell'intera suite: senza reset, il contatore GLOBALE (difesa
anti brute-force distribuito, SECURITY_REPORT #2) si accumulerebbe di test in test
e bloccherebbe richieste in test successivi. Anche la "generazione" dei token
(revoca al logout, #3) e' stato di processo. Questa fixture autouse azzera
entrambi prima e dopo OGNI test.

Import lazy dentro la fixture: cosi' non si importa ``app`` (che congela
``get_settings`` con ``@lru_cache``) prima che i moduli di test abbiano impostato
i loro env in fase di collection.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_auth_state():
    try:
        from app.auth import _reset_generation_for_tests
        from app.security import get_login_rate_limiter
    except Exception:
        # moduli di test puri che non usano l'app: niente da isolare
        yield
        return
    get_login_rate_limiter().clear()
    _reset_generation_for_tests()
    yield
    get_login_rate_limiter().clear()
    _reset_generation_for_tests()
