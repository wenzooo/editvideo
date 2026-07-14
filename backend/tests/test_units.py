"""Unit test delle funzioni pure: timeline (keep/remap), captions, styles."""
import pytest

from app.services.captions import chunk_words
from app.services.styles import build_ass, hex_to_ass_colour, STYLES
from app.services.timeline import keep_intervals, map_time, output_duration, remap_segments


def test_keep_intervals_no_edits():
    assert keep_intervals(10.0) == [(0.0, 10.0)]


def test_keep_intervals_trim_and_cuts():
    keeps = keep_intervals(10.0, trim_start=1.0, trim_end=9.0,
                           cuts=[{"start": 3.0, "end": 4.0}, {"start": 6.0, "end": 7.5}])
    assert keeps == [(1.0, 3.0), (4.0, 6.0), (7.5, 9.0)]
    assert output_duration(keeps) == pytest.approx(5.5)


def test_keep_intervals_overlapping_cuts_merged():
    keeps = keep_intervals(10.0, cuts=[{"start": 2, "end": 5}, {"start": 4, "end": 6}])
    assert keeps == [(0.0, 2.0), (6.0, 10.0)]


def test_keep_intervals_everything_cut_raises():
    with pytest.raises(ValueError):
        keep_intervals(10.0, cuts=[{"start": 0, "end": 10}])


def test_map_time_piecewise():
    keeps = [(1.0, 3.0), (4.0, 6.0)]
    assert map_time(0.5, keeps) == 0.0          # prima del primo keep
    assert map_time(2.0, keeps) == 1.0          # dentro il primo keep
    assert map_time(3.5, keeps) == 2.0          # dentro il taglio -> collassa
    assert map_time(5.0, keeps) == 3.0          # dentro il secondo keep
    assert map_time(9.0, keeps) == 4.0          # dopo tutto


def test_remap_segments_across_cut():
    keeps = [(0.0, 3.0), (5.0, 10.0)]
    segs = [
        (1.0, 2.0, "dentro"),          # invariato
        (3.5, 4.5, "tagliato"),        # interamente nel cut -> sparisce
        (2.5, 6.0, "a cavallo"),       # si accorcia: 2.5->2.5, 6.0->4.0
    ]
    out = remap_segments(segs, keeps)
    assert out == [(1.0, 2.0, "dentro"), (2.5, 4.0, "a cavallo")]


def test_chunk_words_max_chars_and_punct():
    words = [(0.0, 0.3, "Ciao"), (0.35, 0.6, "a"), (0.65, 1.0, "tutti."),
             (1.2, 1.5, "Oggi"), (1.55, 2.0, "parliamo"), (2.05, 2.5, "di"),
             (2.55, 3.0, "montaggio")]
    chunks = chunk_words(words, max_chars=20, max_gap=0.8)
    assert chunks[0][2] == "Ciao a tutti."       # chiusa dalla punteggiatura
    assert all(len(c[2]) <= 20 for c in chunks)
    assert chunks[0][0] == 0.0 and chunks[0][1] == 1.0


def test_chunk_words_gap_split():
    words = [(0.0, 0.5, "prima"), (3.0, 3.5, "dopo")]
    chunks = chunk_words(words, max_chars=42, max_gap=0.8)
    assert len(chunks) == 2


def test_build_ass_all_styles():
    segs = [(0.0, 1.5, "Prova {tag} ciao"), (2.0, 3.0, "riga\ndue")]
    for style_id in STYLES:
        content = build_ass(segs, style_id, font="DejaVu Sans")
        assert "[Script Info]" in content
        assert "PlayResX: 1080" in content and "PlayResY: 1920" in content
        assert "Dialogue: 0,0:00:00.00,0:00:01.50,Default" in content
        assert "{" not in content.split("[Events]")[1]  # niente override tag iniettati
        assert "\\N" in content


# --------------------------------------------------------------------------- #
# styles: colore karaoke configurabile (hex -> ASS BGR) + build_ass
# --------------------------------------------------------------------------- #
def test_hex_to_ass_colour_bgr_conversion():
    # ASS usa BGR con alpha 00: "#RRGGBB" -> "&H00BBGGRR&"
    assert hex_to_ass_colour("#FF0000") == "&H000000FF&"   # rosso
    assert hex_to_ass_colour("#00FF00") == "&H0000FF00&"   # verde
    assert hex_to_ass_colour("#0000FF") == "&H00FF0000&"   # blu
    # cancelletto opzionale + case-insensitive
    assert hex_to_ass_colour("ff0000") == "&H000000FF&"


def test_hex_to_ass_colour_invalid_falls_back_to_yellow():
    yellow = "&H0000FFFF&"
    assert hex_to_ass_colour(None) == yellow
    assert hex_to_ass_colour("") == yellow
    assert hex_to_ass_colour("#12345") == yellow      # lunghezza errata
    assert hex_to_ass_colour("#GGGGGG") == yellow      # cifre non esadecimali
    assert hex_to_ass_colour(123) == yellow            # tipo non stringa


_KARAOKE_SEGS = [{
    "start": 0.0, "end": 2.0, "text": "ciao mondo",
    "words": [[0.0, 1.0, "ciao"], [1.0, 2.0, "mondo"]],
}]


def test_build_ass_karaoke_uses_configured_colour():
    content = build_ass(_KARAOKE_SEGS, "karaoke_word", karaoke_color="#FF0000")
    assert "\\1c&H000000FF&" in content       # parola attiva in rosso
    assert "\\1c&H0000FFFF&" not in content    # nessun giallo residuo
    assert "\\1c&H00FFFFFF&" in content        # base colour resta bianco


def test_build_ass_karaoke_defaults_to_yellow_when_colour_absent():
    content = build_ass(_KARAOKE_SEGS, "karaoke_word")
    assert "\\1c&H0000FFFF&" in content        # giallo di default (retro-compatibile)
    # anche un colore invalido ricade sul giallo
    assert "\\1c&H0000FFFF&" in build_ass(_KARAOKE_SEGS, "karaoke_word", karaoke_color="boh")


def test_build_ass_non_karaoke_ignores_colour():
    # per gli stili non-karaoke il colore non introduce override tag
    content = build_ass(_KARAOKE_SEGS, "classic_white", karaoke_color="#FF0000")
    assert "\\1c" not in content.split("[Events]")[1]
