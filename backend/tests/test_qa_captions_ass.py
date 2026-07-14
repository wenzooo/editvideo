"""QA: chunking caption (services/captions.py) e generazione .ass (services/styles.py).

Moduli PURI: import diretto dei servizi, nessun preambolo env necessario
(build_ass usa get_settings() solo per font/spacing di default, con default sicuri).
Complementare a test_units.py e test_sub_position.py: qui si coprono i casi
limite non gia' testati (invariante di non sovrapposizione, timestamp canonici,
finestre karaoke collassate, fallback e clamp).
"""
import pytest

from app.services.captions import chunk_words, chunk_words_detailed
from app.services.styles import (
    DEFAULT_STYLE,
    PLAY_H,
    PLAY_W,
    STYLES,
    _ass_time,
    _escape_text,
    _karaoke_events,
    _pos_tag,
    build_ass,
    hex_to_ass_colour,
)


def _dialogues(ass: str) -> list[str]:
    return [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]


def _style_fontsize(ass: str) -> int:
    line = next(ln for ln in ass.splitlines() if ln.startswith("Style: Default,"))
    return int(line.split(",")[2])


# --------------------------------------------------------------------------- #
# TEST ROSSI — bug confermati
# --------------------------------------------------------------------------- #

# BUG CONFERMATO QA-03: captions.py:36 forza end = max(cur_end, cur_start + 0.2);
# una caption chiusa da punteggiatura forte con parola brevissima (0.0-0.1)
# viene estesa a 0.2 e si SOVRAPPONE alla caption successiva (start 0.15) —
# vedi TEST_REPORT.md
def test_chunks_never_overlap_even_with_min_duration_padding():
    words = [(0.0, 0.1, "Ciao."), (0.15, 0.5, "mondo"), (0.55, 0.9, "bello")]
    chunks = chunk_words_detailed(words)
    assert len(chunks) == 2  # "Ciao." chiusa dal punto, poi "mondo bello"
    for cur, nxt in zip(chunks, chunks[1:]):
        # invariante corretto: nessuna sovrapposizione tra caption consecutive
        assert cur["end"] <= nxt["start"], (
            f"caption {cur['text']!r} finisce a {cur['end']} ma la successiva "
            f"{nxt['text']!r} inizia a {nxt['start']}"
        )


# BUG CONFERMATO QA-04: styles.py:_ass_time formatta i secondi senza riportare
# il resto sui minuti: _ass_time(59.999) produce "0:00:60.00" (secondi = 60,
# timestamp non canonico) invece di "0:01:00.00" — vedi TEST_REPORT.md
def test_ass_time_rounds_up_into_minutes_canonically():
    assert _ass_time(59.999) == "0:01:00.00"


# --------------------------------------------------------------------------- #
# VERDI — captions.py
# --------------------------------------------------------------------------- #

def test_chunk_words_filters_empty_and_whitespace_words():
    words = [(0.0, 0.1, ""), (0.15, 0.3, "   "), (0.4, 0.8, "ciao"),
             (0.85, 1.2, "\t"), (1.3, 1.7, "mondo")]
    chunks = chunk_words_detailed(words)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "ciao mondo"
    # le parole vuote non finiscono nemmeno nella lista words della caption
    assert [w[2] for w in chunks[0]["words"]] == ["ciao", "mondo"]
    # input di sole parole vuote -> nessuna caption
    assert chunk_words_detailed([(0.0, 0.5, "  "), (0.6, 1.0, "")]) == []


@pytest.mark.parametrize("punct", ["!", "?", "…"])
def test_strong_punctuation_closes_caption(punct):
    # "…" incluso tra la punteggiatura forte (oltre a . ! ?)
    words = [(0.0, 0.5, f"Aspetta{punct}"), (0.6, 1.0, "ok")]
    chunks = chunk_words(words, max_chars=42, max_gap=0.8)
    assert len(chunks) == 2
    assert chunks[0][2] == f"Aspetta{punct}"
    assert chunks[1][2] == "ok"


def test_max_gap_boundary_opens_new_caption_only_when_exceeded():
    # gap 0.7 <= max_gap: stessa caption
    same = chunk_words_detailed([(0.0, 0.5, "prima"), (1.2, 1.6, "poi")], max_gap=0.8)
    assert len(same) == 1 and same[0]["text"] == "prima poi"
    # gap 0.9 > max_gap: nuova caption, ognuna con le proprie words
    split = chunk_words_detailed([(0.0, 0.5, "prima"), (1.4, 1.8, "poi")], max_gap=0.8)
    assert len(split) == 2
    assert [c["text"] for c in split] == ["prima", "poi"]
    assert split[0]["words"] == [[0.0, 0.5, "prima"]]
    assert split[1]["words"] == [[1.4, 1.8, "poi"]]


def test_single_word_longer_than_max_chars_kept_whole():
    words = [(0.0, 1.0, "supercalifragilistichespiralidoso"), (1.1, 1.5, "si")]
    chunks = chunk_words(words, max_chars=10, max_gap=0.8)
    # la parola oltre max_chars non viene spezzata ne' scartata
    assert chunks[0][2] == "supercalifragilistichespiralidoso"
    assert chunks[1][2] == "si"


def test_chunk_words_is_detailed_without_words_field():
    words = [(0.0, 0.3, "Ciao"), (0.35, 0.6, "a"), (0.65, 1.0, "tutti."),
             (2.5, 3.0, "Poi"), (3.05, 3.5, "altro")]
    detailed = chunk_words_detailed(words, max_chars=20, max_gap=0.8)
    compat = chunk_words(words, max_chars=20, max_gap=0.8)
    assert compat == [(c["start"], c["end"], c["text"]) for c in detailed]


# --------------------------------------------------------------------------- #
# VERDI — styles.py: _escape_text
# --------------------------------------------------------------------------- #

def test_escape_text_neutralizes_braces_and_converts_newlines():
    assert _escape_text("ciao {\\b1}mondo{\\b0}") == "ciao (\\b1)mondo(\\b0)"
    assert _escape_text("riga\nuno\r\ndue") == "riga\\Nuno\\Ndue"


def test_escape_text_backslash_not_escaped_characterization():
    # CHARACTERIZATION (QA): il backslash NON viene gestito — una r"\N" letterale
    # nel testo resta tale e quale, e libass la interpreta come a-capo forzato.
    # Non e' iniettabile come override tag (servono le graffe, neutralizzate),
    # ma il comportamento e' documentato qui per non regredire inconsapevolmente.
    assert _escape_text(r"\N") == "\\N"
    assert _escape_text(r"prezzo 10\Neuro") == "prezzo 10\\Neuro"


# --------------------------------------------------------------------------- #
# VERDI — styles.py: _karaoke_events
# --------------------------------------------------------------------------- #

def test_karaoke_collapsed_window_skipped_without_crash():
    # due parole con lo stesso start (residuo di un taglio): la finestra della
    # prima e' [0.0, 0.0] -> saltata; le altre due producono eventi regolari
    words = [[0.0, 0.5, "ciao"], [0.0, 0.9, "mondo"], [1.0, 2.0, "bello"]]
    events = _karaoke_events(0.0, 2.0, words)
    assert len(events) == 2
    # il primo evento evidenzia "mondo" (indice 1), non "ciao"
    assert "{\\1c&H0000FFFF&}mondo{\\1c&H00FFFFFF&}" in events[0]
    assert "{\\1c&H0000FFFF&}ciao" not in events[0]


def test_karaoke_words_entirely_outside_segment_all_skipped():
    # tutte le finestre clampate a [seg_start, seg_end] collassano -> zero eventi
    words = [[5.0, 5.5, "fuori"], [5.6, 6.0, "tempo"]]
    assert _karaoke_events(0.0, 2.0, words) == []


def test_karaoke_last_word_extends_to_segment_end():
    words = [[0.0, 0.4, "ciao"], [0.5, 0.8, "mondo"]]
    events = _karaoke_events(0.0, 3.25, words)
    assert len(events) == 2
    # l'ultima parola resta evidenziata fino alla fine della caption
    assert events[-1].startswith(f"Dialogue: 0,{_ass_time(0.5)},{_ass_time(3.25)},")


def test_karaoke_pos_tag_prepended_to_every_event():
    tag = "{\\an5\\pos(540,960)}"
    words = [[0.0, 0.4, "uno"], [0.5, 0.9, "due"], [1.0, 1.9, "tre"]]
    events = _karaoke_events(0.0, 2.0, words, pos_tag=tag)
    assert len(events) == 3
    for ev in events:
        _, text = ev.split(",,0,0,0,,", 1)
        assert text.startswith(tag)


def test_karaoke_custom_active_colour_from_hex():
    colour = hex_to_ass_colour("#00FF00")  # verde -> &H0000FF00&
    events = _karaoke_events(0.0, 2.0, [[0.0, 1.0, "ciao"], [1.0, 2.0, "mondo"]],
                             active_colour=colour)
    assert colour == "&H0000FF00&"
    assert all(f"{{\\1c{colour}}}" in ev for ev in events)
    # la parola non attiva torna sempre al bianco di base
    assert all("{\\1c&H00FFFFFF&}" in ev for ev in events)


# --------------------------------------------------------------------------- #
# VERDI — styles.py: build_ass
# --------------------------------------------------------------------------- #

def test_build_ass_unknown_style_falls_back_to_default():
    ass = build_ass([(0.0, 1.0, "ciao")], "stile_inesistente", font="TestFont")
    default = STYLES[DEFAULT_STYLE]
    assert _style_fontsize(ass) == default.fontsize
    style_line = next(ln for ln in ass.splitlines() if ln.startswith("Style: Default,"))
    assert style_line.startswith(f"Style: Default,TestFont,{default.fontsize},{default.primary},")
    assert len(_dialogues(ass)) == 1


def test_build_ass_karaoke_without_words_renders_plain_dialogue():
    segs = [
        {"start": 0.0, "end": 1.5, "text": "editata a mano"},   # dict senza words
        (2.0, 3.0, "tupla semplice"),                            # tupla compat
    ]
    ass = build_ass(segs, "karaoke_word", font="TestFont")
    dialogues = _dialogues(ass)
    assert len(dialogues) == 2  # un Dialogue per segmento, non per parola
    assert "\\1c" not in ass.split("[Events]")[1]
    assert dialogues[0].endswith(",,0,0,0,,editata a mano")
    assert dialogues[1].endswith(",,0,0,0,,tupla semplice")


def test_build_ass_sub_scale_clamped_low_and_high():
    base = STYLES[DEFAULT_STYLE].fontsize  # 92
    tiny = build_ass([(0.0, 1.0, "x")], "karaoke_word", font="TestFont", sub_scale=0.1)
    huge = build_ass([(0.0, 1.0, "x")], "karaoke_word", font="TestFont", sub_scale=99)
    assert _style_fontsize(tiny) == int(round(base * 0.5))   # clamp basso -> x0.5
    assert _style_fontsize(huge) == int(round(base * 2.5))   # clamp alto -> x2.5


def test_pos_tag_none_and_clamped_bounds():
    # None -> nessun override (stringa vuota, il Dialogue usa lo Style)
    assert _pos_tag(None) == ""
    # clamp basso: 0.0 -> 0.05 dell'altezza
    y_low = int(round(PLAY_H * 0.05))
    assert _pos_tag(0.0) == f"{{\\an5\\pos({PLAY_W // 2},{y_low})}}"
    # clamp alto: 1.5 -> 0.95 dell'altezza
    y_high = int(round(PLAY_H * 0.95))
    assert _pos_tag(1.5) == f"{{\\an5\\pos({PLAY_W // 2},{y_high})}}"
