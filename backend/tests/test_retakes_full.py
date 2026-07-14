"""Unit test (puri, offline) della detection combinata dei doppioni:
`detect_all_retake_cuts` gestisce anche le RIPRESE DELL'INTERO discorso
(il soggetto ricomincia da capo piu' volte) tenendo SOLO l'ultima ripresa.

Come gli altri unit test, importa solo i moduli services puri: niente
fastapi/sqlalchemy/whisper, nessun I/O. Non modifica i test esistenti.
"""
import pytest

from app.services.retakes import (
    detect_all_retake_cuts,
    detect_retake_cuts,
    filter_words_outside_cuts,
)

_INCIPIT = ["allora", "ragazzi", "oggi", "vi", "parlo"]


def _take_words(t0: float, body: list[str]) -> list[tuple[float, float, str]]:
    """Un tentativo di ripresa: incipit identico (>=4 parole) + un corpo.
    Le parole partono da t0, una ogni 0.5s (durata 0.3s)."""
    out: list[tuple[float, float, str]] = []
    t = t0
    for word in _INCIPIT + body:
        out.append((round(t, 2), round(t + 0.3, 2), word))
        t += 0.5
    return out


def _no_overlaps(cuts: list[dict]) -> bool:
    return all(cuts[i]["end"] <= cuts[i + 1]["start"] for i in range(len(cuts) - 1))


# --------------------------------------------------------------------------- #
# full-take: lo stesso discorso ripreso piu' volte -> resta l'ultima ripresa
# --------------------------------------------------------------------------- #
def test_full_take_three_repetitions_keeps_only_last():
    # tre riprese con lo stesso incipit a 0s / 40s / 80s: buona = l'ultima
    words = (
        _take_words(0.0, ["del", "montaggio"])
        + _take_words(40.0, ["del", "montaggio"])
        + _take_words(80.0, ["del", "montaggio", "finale"])
    )
    cuts = detect_all_retake_cuts(words)
    # i due tentativi abortiti (0->40 e 40->80) si fondono in un'unica regione.
    # Il confine tiene un piccolo "respiro" (lead-in) prima della ripresa: la
    # ripresa buona parte a 80.0, si taglia fino a 80.0-0.15 = 79.85.
    assert len(cuts) == 1
    assert cuts[0]["start"] == 0.0
    assert cuts[0]["end"] == pytest.approx(79.85, abs=0.02)
    assert _no_overlaps(cuts)
    # sopravvive SOLO l'ultima ripresa completa, intatta
    kept = [w[2] for w in filter_words_outside_cuts(words, cuts)]
    assert kept == _INCIPIT + ["del", "montaggio", "finale"]


def test_full_take_two_repetitions_keeps_last():
    words = (
        _take_words(0.0, ["del", "montaggio"])
        + _take_words(40.0, ["del", "montaggio", "finale"])
    )
    cuts = detect_all_retake_cuts(words)
    assert len(cuts) == 1
    assert cuts[0]["start"] == 0.0
    assert cuts[0]["end"] == pytest.approx(39.85, abs=0.02)
    kept = [w[2] for w in filter_words_outside_cuts(words, cuts)]
    assert kept == _INCIPIT + ["del", "montaggio", "finale"]


def test_full_take_respects_max_cut_full():
    # riprese entro la finestra (window_full ampia) ma piu' distanti di
    # max_cut_full: nessun taglio, cosi' non si collassano discorsi lunghi con
    # un incipit simile molto lontano.
    words = _take_words(0.0, ["del", "montaggio"]) + _take_words(400.0, ["del", "montaggio"])
    assert detect_all_retake_cuts(words, window_full=500.0, max_cut_full=300.0) == []


# --------------------------------------------------------------------------- #
# casi limite: nessuna ripetizione, ripartenza breve ancora gestita
# --------------------------------------------------------------------------- #
def test_no_repetition_returns_no_cuts():
    words = [
        (0.0, 0.3, "uno"), (0.5, 0.8, "due"), (1.0, 1.3, "tre"),
        (1.5, 1.8, "quattro"), (2.0, 2.3, "cinque"), (2.5, 2.8, "sei"),
    ]
    assert detect_all_retake_cuts(words) == []


def test_empty_words_returns_no_cuts():
    assert detect_all_retake_cuts([]) == []


def test_short_local_retake_still_handled():
    # ripartenza BREVE classica: la passata locale deve continuare a rilevarla,
    # identica a detect_retake_cuts (retro-compat della firma esistente)
    words = [
        (0.0, 0.3, "Quindi"), (0.3, 0.6, "noi"), (0.6, 0.9, "andiamo"),
        (1.0, 1.3, "quindi"), (1.3, 1.6, "noi"), (1.6, 1.9, "andiamo"),
        (2.0, 2.5, "bene"),
    ]
    expected = [{"start": 0.0, "end": 0.94}]
    assert detect_retake_cuts(words, min_match=3) == expected  # firma invariata
    assert detect_all_retake_cuts(words) == expected


# --------------------------------------------------------------------------- #
# locale + full insieme: tagli disgiunti, mai sovrapposti
# --------------------------------------------------------------------------- #
def test_combined_local_and_full_no_overlaps():
    # take1 abortita a 0s; ripresa PIENA a 60s (l'ultima, buona) che al suo
    # interno contiene una ripartenza BREVE ("e quindi iniziamo" x2).
    words = [
        # take1 (abortita)
        (0.0, 0.3, "ciao"), (0.4, 0.7, "a"), (0.8, 1.1, "tutti"),
        (1.2, 1.5, "oggi"), (1.6, 1.9, "parliamo"), (2.0, 2.3, "di"), (2.4, 2.7, "montaggio"),
        # take2 (ultima, buona): stesso incipit molto piu' avanti
        (60.0, 60.3, "ciao"), (60.4, 60.7, "a"), (60.8, 61.1, "tutti"),
        (61.2, 61.5, "oggi"), (61.6, 61.9, "parliamo"), (62.0, 62.3, "di"), (62.4, 62.7, "montaggio"),
        # ripartenza breve DENTRO l'ultima ripresa
        (63.0, 63.3, "e"), (63.4, 63.7, "quindi"), (63.8, 64.1, "iniziamo"),
        (64.3, 64.6, "e"), (64.7, 65.0, "quindi"), (65.1, 65.4, "iniziamo"),
        (65.5, 65.8, "davvero"),
    ]
    cuts = detect_all_retake_cuts(words)
    # due tagli distinti: la ripresa piena (0->60) e la ripartenza breve interna
    assert len(cuts) == 2
    assert _no_overlaps(cuts)
    assert cuts[0]["start"] == 0.0 and cuts[0]["end"] == pytest.approx(59.85, abs=0.02)
    assert cuts[1]["start"] == pytest.approx(62.94, abs=0.02)
    assert cuts[1]["end"] == pytest.approx(64.24, abs=0.02)
    # resta l'ultima ripresa, ripulita anche dalla ripartenza breve interna
    kept = [w[2] for w in filter_words_outside_cuts(words, cuts)]
    assert kept == ["ciao", "a", "tutti", "oggi", "parliamo", "di", "montaggio",
                    "e", "quindi", "iniziamo", "davvero"]


def test_high_min_match_full_avoids_false_positive():
    # solo 3 parole d'incipit ripetute a distanza: sotto min_match_full=4 la
    # passata "full" NON deve tagliare (troppo debole come segnale a distanza)
    words = [
        (0.0, 0.3, "buongiorno"), (0.5, 0.8, "a"), (1.0, 1.3, "voi"),
        (1.5, 1.8, "oggi"), (2.0, 2.3, "spieghero"),
        (60.0, 60.3, "buongiorno"), (60.5, 60.8, "a"), (61.0, 61.3, "voi"),
        (61.5, 61.8, "invece"), (62.0, 62.3, "faremo"),
    ]
    # finestra full ampia ma solo 3 parole coincidono -> nessun taglio a distanza
    assert detect_all_retake_cuts(words) == []
