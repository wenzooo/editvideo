"""Test delle funzioni pure dei feature flag (nessun env / nessuna app).

``parse_flags`` e' puro; ``feature_flags`` / ``is_enabled`` leggono la config, ma
qui vengono resi ermetici sostituendo ``get_settings`` con un finto (monkeypatch),
cosi' il test non dipende dall'ambiente reale.
"""
from types import SimpleNamespace

from app.services import flags
from app.services.flags import feature_flags, is_enabled, parse_flags


# --------------------------------------------------------------------------- #
# parse_flags: funzione pura
# --------------------------------------------------------------------------- #
def test_parse_flags_on_off():
    assert parse_flags("chaos_probe=0,new_export=1") == {
        "chaos_probe": False,
        "new_export": True,
    }


def test_parse_flags_truthy_synonyms():
    parsed = parse_flags("a=true,b=on,c=YES,d=1")
    assert parsed == {"a": True, "b": True, "c": True, "d": True}


def test_parse_flags_falsy_values():
    # tutto cio' che non e' un sinonimo di vero -> False
    parsed = parse_flags("a=0,b=false,c=off,d=no,e=random")
    assert parsed == {"a": False, "b": False, "c": False, "d": False, "e": False}


def test_parse_flags_bare_name_is_enabled():
    # nome nudo senza "=" -> acceso
    assert parse_flags("new_export") == {"new_export": True}
    assert parse_flags("new_export,chaos_probe=0") == {
        "new_export": True,
        "chaos_probe": False,
    }


def test_parse_flags_ignores_whitespace_and_empty_tokens():
    assert parse_flags("  a = 1 , , b=0 ,") == {"a": True, "b": False}


def test_parse_flags_ignores_empty_name():
    # token "=1" ha nome vuoto: scartato
    assert parse_flags("=1,valid=1") == {"valid": True}


def test_parse_flags_empty_string_and_none():
    assert parse_flags("") == {}
    assert parse_flags(None) == {}  # type: ignore[arg-type]


def test_parse_flags_duplicate_last_wins():
    assert parse_flags("a=1,a=0") == {"a": False}


# --------------------------------------------------------------------------- #
# is_enabled / feature_flags: wrapper sulla config (monkeypatch ermetico)
# --------------------------------------------------------------------------- #
def _patch(monkeypatch, raw: str) -> None:
    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(feature_flags=raw))


def test_is_enabled_true_and_false(monkeypatch):
    _patch(monkeypatch, "new_export=1,chaos_probe=0")
    assert is_enabled("new_export") is True
    assert is_enabled("chaos_probe") is False


def test_is_enabled_unknown_defaults_false(monkeypatch):
    _patch(monkeypatch, "new_export=1")
    assert is_enabled("mai_definito") is False


def test_is_enabled_empty_config_all_false(monkeypatch):
    _patch(monkeypatch, "")
    assert is_enabled("qualsiasi") is False


def test_feature_flags_reads_config(monkeypatch):
    _patch(monkeypatch, "a=1,b=0")
    assert feature_flags() == {"a": True, "b": False}
