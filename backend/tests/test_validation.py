"""Unit test aggiuntivi: casi limite delle funzioni PURE (nessun ffmpeg / whisper
a runtime) e validazione degli schemi Pydantic.

Copre i casi non gia' presenti in test_units.py:
- timeline: normalize_cuts, finestra di trim, rimappatura dettagliata delle parole;
- captions: input vuoti, parole lunghe, durata minima;
- silence: conversione pause->tagli (respiro/leave, bordi, soglia);
- retakes: match delle ripetizioni, normalizzazione, filtro parole;
- formats: applicazione di un Format (clamp, casi non applicabili);
- schemas: accettazione degli input validi e rifiuto di quelli malformati.

I moduli services sono importati direttamente cosi' da NON tirare dentro
fastapi/sqlalchemy/faster-whisper: i test restano leggeri e offline.
"""
import math

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas import CutRange, SubtitleSegmentIn, SubtitleSegmentOut, TemplateIn, VideoPatch
from app.services.captions import chunk_words, chunk_words_detailed
from app.services.formats import apply_template
from app.services.retakes import _norm, detect_retake_cuts, filter_words_outside_cuts
from app.services.silence import silences_to_cuts
from app.services.styles import STYLES
from app.services.timeline import (
    keep_intervals,
    map_time,
    normalize_cuts,
    output_duration,
    remap_segments,
    remap_segments_detailed,
)


# --------------------------------------------------------------------------- #
# timeline
# --------------------------------------------------------------------------- #
def test_normalize_cuts_clamps_and_drops_out_of_range():
    # start negativo -> 0; cut completamente oltre la durata -> scartato
    assert normalize_cuts([{"start": -5, "end": 3}, {"start": 100, "end": 200}], 10) == [(0.0, 3.0)]


def test_normalize_cuts_merges_touching_intervals():
    # intervalli adiacenti (che si toccano) vengono fusi in uno solo
    assert normalize_cuts([{"start": 2, "end": 5}, {"start": 5, "end": 8}], 10) == [(2.0, 8.0)]


def test_normalize_cuts_drops_sub_min_interval():
    # un cut piu' corto di MIN_INTERVAL (0.05s) viene scartato
    assert normalize_cuts([{"start": 2.0, "end": 2.02}], 10) == []


def test_normalize_cuts_sorts_unsorted_input():
    assert normalize_cuts([{"start": 6, "end": 7}, {"start": 1, "end": 2}], 10) == [(1.0, 2.0), (6.0, 7.0)]


def test_keep_intervals_default_trim_end_is_duration():
    assert keep_intervals(10.0, trim_start=2.0) == [(2.0, 10.0)]


def test_keep_intervals_ignores_cuts_outside_trim_window():
    keeps = keep_intervals(10.0, 2.0, 8.0, [{"start": 0, "end": 1}, {"start": 9, "end": 10}])
    assert keeps == [(2.0, 8.0)]


def test_keep_intervals_cut_straddling_trim_start_clamped():
    # un cut che sconfina prima dell'inizio del trim viene tagliato al bordo
    assert keep_intervals(10.0, 2.0, 8.0, [{"start": 1, "end": 3}]) == [(3.0, 8.0)]


def test_keep_intervals_cut_straddling_trim_end_clamped():
    assert keep_intervals(10.0, 2.0, 8.0, [{"start": 7, "end": 9}]) == [(2.0, 7.0)]


def test_keep_intervals_empty_trim_window_raises():
    with pytest.raises(ValueError):
        keep_intervals(10.0, trim_start=5.0, trim_end=5.0)


def test_keep_intervals_trim_start_beyond_duration_raises():
    with pytest.raises(ValueError):
        keep_intervals(10.0, trim_start=100.0)


def test_map_time_at_keep_boundaries():
    keeps = [(1.0, 3.0), (4.0, 6.0)]
    assert map_time(1.0, keeps) == 0.0   # inizio del primo keep
    assert map_time(3.0, keeps) == 2.0   # fine del primo keep
    assert map_time(4.0, keeps) == 2.0   # inizio del secondo keep (il taglio collassa)
    assert map_time(6.0, keeps) == 4.0   # fine del secondo keep


def test_output_duration_sums_keeps():
    assert output_duration([(1.0, 3.0), (5.0, 10.0)]) == pytest.approx(7.0)


def test_remap_segments_segment_inside_cut_disappears():
    keeps = [(0.0, 3.0), (5.0, 10.0)]
    assert remap_segments([(3.5, 4.5, "tagliato")], keeps) == []


def test_remap_segments_detailed_words_across_cut():
    # parola centrale dentro il taglio -> scartata; le altre restano contigue
    segs = [{
        "start": 2.0, "end": 6.0, "text": "cross",
        "words": [[2.0, 2.4, "a"], [3.2, 3.6, "b"], [5.2, 5.6, "c"]],
    }]
    keeps = [(0.0, 3.0), (5.0, 10.0)]
    out = remap_segments_detailed(segs, keeps)
    assert out == [{
        "start": 2.0, "end": 4.0, "text": "cross",
        "words": [[2.0, 2.4, "a"], [3.2, 3.6, "c"]],
    }]


def test_remap_segments_detailed_drops_empty_text():
    keeps = [(0.0, 10.0)]
    assert remap_segments_detailed([{"start": 1.0, "end": 2.0, "text": "   "}], keeps) == []


def test_remap_segments_detailed_without_words_keeps_none():
    keeps = [(0.0, 10.0)]
    out = remap_segments_detailed([{"start": 1.0, "end": 2.0, "text": "ciao"}], keeps)
    assert out == [{"start": 1.0, "end": 2.0, "text": "ciao", "words": None}]


# --------------------------------------------------------------------------- #
# captions
# --------------------------------------------------------------------------- #
def test_chunk_words_empty_input():
    assert chunk_words([]) == []


def test_chunk_words_whitespace_only_dropped():
    assert chunk_words([(0.0, 0.5, "   "), (0.6, 1.0, "")]) == []


def test_chunk_words_long_word_not_split():
    # una singola parola piu' lunga di max_chars non va persa: resta in una caption
    chunks = chunk_words([(0.0, 1.0, "supercalifragilistic")], max_chars=5)
    assert chunks == [(0.0, 1.0, "supercalifragilistic")]


def test_chunk_words_two_long_words_split():
    chunks = chunk_words([(0.0, 0.5, "abcdefgh"), (0.6, 1.0, "ijklmnop")], max_chars=5)
    assert [c[2] for c in chunks] == ["abcdefgh", "ijklmnop"]


def test_chunk_words_max_chars_boundary():
    # "abcd efgh" = 9 char, entra con max_chars=10; "ijkl" apre una nuova caption
    chunks = chunk_words(
        [(0.0, 0.3, "abcd"), (0.4, 0.7, "efgh"), (0.8, 1.1, "ijkl")], max_chars=10,
    )
    assert [c[2] for c in chunks] == ["abcd efgh", "ijkl"]


def test_chunk_words_min_duration_enforced():
    # end viene alzato ad almeno start + 0.2
    assert chunk_words([(0.0, 0.1, "hi.")]) == [(0.0, 0.2, "hi.")]


def test_chunk_words_detailed_preserves_words():
    out = chunk_words_detailed([(0.0, 0.3, "Ciao"), (0.4, 0.7, "mondo.")], max_chars=42)
    assert out == [{
        "start": 0.0, "end": 0.7, "text": "Ciao mondo.",
        "words": [[0.0, 0.3, "Ciao"], [0.4, 0.7, "mondo."]],
    }]


# --------------------------------------------------------------------------- #
# silence (solo silences_to_cuts: puro, niente ffmpeg)
# --------------------------------------------------------------------------- #
def test_silences_internal_pause_centered_with_leave():
    # pausa interna [3,5]: si taglia il centro lasciando leave/2 per lato
    assert silences_to_cuts([(3.0, 5.0)], 10.0) == [{"start": 3.12, "end": 4.88}]


def test_silences_start_pause_cut_from_zero():
    assert silences_to_cuts([(0.0, 2.0)], 10.0) == [{"start": 0.0, "end": 1.88}]


def test_silences_end_pause_open_to_duration():
    # fine=None: la pausa arriva a fine video e il taglio va fino alla durata
    assert silences_to_cuts([(8.0, None)], 10.0) == [{"start": 8.12, "end": 10.0}]


def test_silences_tiny_pause_below_min_cut_dropped():
    assert silences_to_cuts([(3.0, 3.15)], 10.0) == []


def test_silences_leave_zero_keeps_full_pause():
    assert silences_to_cuts([(3.0, 5.0)], 10.0, leave=0.0) == [{"start": 3.0, "end": 5.0}]


def test_silences_multiple_pauses():
    cuts = silences_to_cuts([(1.0, 2.0), (5.0, 6.0)], 10.0)
    assert cuts == [{"start": 1.12, "end": 1.88}, {"start": 5.12, "end": 5.88}]


# --------------------------------------------------------------------------- #
# retakes
# --------------------------------------------------------------------------- #
def _retake_words():
    return [
        (0.0, 0.3, "Quindi"), (0.3, 0.6, "noi"), (0.6, 0.9, "andiamo"),
        (1.0, 1.3, "quindi"), (1.3, 1.6, "noi"), (1.6, 1.9, "andiamo"),
        (2.0, 2.5, "bene"),
    ]


def test_detect_retake_basic_repetition():
    # il primo tentativo (0 -> ~ripresa) viene proposto come taglio
    assert detect_retake_cuts(_retake_words(), min_match=3) == [{"start": 0.0, "end": 0.94}]


def test_detect_retake_no_repetition_returns_empty():
    words = [(0, 0.3, "a"), (0.4, 0.7, "b"), (0.8, 1.1, "c"), (1.2, 1.5, "d")]
    assert detect_retake_cuts(words, min_match=3) == []


def test_detect_retake_below_min_match_ignored():
    # ripetizione di sole 2 parole con min_match=3 -> nessun taglio
    words = [(0, 0.3, "ok"), (0.3, 0.6, "via"), (0.7, 1.0, "ok"), (1.0, 1.3, "via"), (1.4, 1.7, "fine")]
    assert detect_retake_cuts(words, min_match=3) == []


def test_detect_retake_outside_window_ignored():
    # la ripresa arriva troppo tardi rispetto a window_s -> non e' un doppione
    words = [
        (0.0, 0.3, "a"), (0.3, 0.6, "b"), (0.6, 0.9, "c"),
        (5.0, 5.3, "a"), (5.3, 5.6, "b"), (5.6, 5.9, "c"),
    ]
    assert detect_retake_cuts(words, min_match=3, window_s=1.0) == []


def test_norm_strips_accents_case_and_punctuation():
    assert _norm("Pero',") == "pero"
    assert _norm("perche'!") == "perche"
    assert _norm("  CIAO ") == "ciao"


def test_filter_words_outside_cuts_removes_inside():
    kept = filter_words_outside_cuts(_retake_words(), [{"start": 0.0, "end": 0.95}])
    assert [w[2] for w in kept] == ["quindi", "noi", "andiamo", "bene"]


def test_filter_words_no_cuts_returns_input():
    words = _retake_words()
    assert filter_words_outside_cuts(words, []) == words


# --------------------------------------------------------------------------- #
# formats.apply_template (puro tramite timeline; video/template finti)
# --------------------------------------------------------------------------- #
def _fake_video(**kw):
    base = dict(duration=10.0, trim_start=-1, trim_end=-1, cuts=None, subtitle_style="ZZZ",
                intro_zoom=None, auto_silence=None, auto_retakes=None, auto_export=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _fake_template(**kw):
    base = dict(trim_start=1.0, tail_trim=1.0, cuts=[{"start": 3.0, "end": 4.0}],
                subtitle_style="classic_yellow")
    base.update(kw)
    return SimpleNamespace(**base)


def test_apply_template_applicable():
    v = _fake_video()
    assert apply_template(v, _fake_template()) is True
    assert v.trim_start == 1.0
    assert v.trim_end == 9.0                       # durata 10 - coda 1
    assert v.cuts == [{"start": 3.0, "end": 4.0}]
    assert v.subtitle_style == "classic_yellow"
    # automazioni ATTIVE DI DEFAULT: i flag mancanti sul template -> getattr(..., True)
    assert v.intro_zoom is True
    assert v.auto_silence is True and v.auto_retakes is True


def test_apply_template_zero_duration_returns_false_untouched():
    v = _fake_video(duration=0)
    assert apply_template(v, _fake_template()) is False
    assert v.trim_start == -1                       # non mutato


def test_apply_template_everything_cut_returns_false_untouched():
    v = _fake_video()
    tpl = _fake_template(trim_start=0.0, tail_trim=0.0, cuts=[{"start": 0, "end": 10}])
    assert apply_template(v, tpl) is False
    assert v.trim_start == -1                       # non mutato


def test_apply_template_trim_start_beyond_resets_to_zero():
    v = _fake_video()
    assert apply_template(v, _fake_template(trim_start=10.0, tail_trim=0.0, cuts=[])) is True
    assert v.trim_start == 0.0


def test_apply_template_tail_leaving_no_room_gives_no_trim_end():
    v = _fake_video()
    assert apply_template(v, _fake_template(trim_start=1.0, tail_trim=9.0, cuts=[])) is True
    assert v.trim_end is None


def test_apply_template_clamps_out_of_range_cuts():
    v = _fake_video()
    tpl = _fake_template(trim_start=0.0, tail_trim=0.0,
                         cuts=[{"start": -2, "end": 3}, {"start": 8, "end": 100}])
    assert apply_template(v, tpl) is True
    assert v.cuts == [{"start": 0.0, "end": 3.0}, {"start": 8.0, "end": 10.0}]


# --------------------------------------------------------------------------- #
# schemas: CutRange
# --------------------------------------------------------------------------- #
def test_cutrange_valid():
    c = CutRange(start=1.0, end=2.5)
    assert (c.start, c.end) == (1.0, 2.5)


def test_cutrange_rejects_negative_start():
    with pytest.raises(ValidationError):
        CutRange(start=-1.0, end=2.0)


def test_cutrange_rejects_end_not_after_start():
    with pytest.raises(ValidationError):
        CutRange(start=5.0, end=5.0)


def test_cutrange_rejects_zero_end():
    with pytest.raises(ValidationError):
        CutRange(start=0.0, end=0.0)


def test_cutrange_rejects_infinity():
    with pytest.raises(ValidationError):
        CutRange(start=1.0, end=math.inf)


def test_cutrange_rejects_nan():
    with pytest.raises(ValidationError):
        CutRange(start=math.nan, end=2.0)


# --------------------------------------------------------------------------- #
# schemas: SubtitleSegmentIn
# --------------------------------------------------------------------------- #
def test_subtitle_segment_valid_with_default_text():
    s = SubtitleSegmentIn(start=0.0, end=1.0)
    assert s.text == "" and s.end == 1.0


def test_subtitle_segment_tolerates_end_le_start():
    # DI PROPOSITO accettato dallo schema: e' il router a filtrare i segmenti
    # degeneri (end <= start) invece di far fallire l'intera richiesta.
    s = SubtitleSegmentIn(start=5.0, end=3.0, text="x")
    assert s.end == 3.0


def test_subtitle_segment_rejects_infinity():
    with pytest.raises(ValidationError):
        SubtitleSegmentIn(start=0.0, end=math.inf, text="x")


def test_subtitle_segment_rejects_negative_start():
    with pytest.raises(ValidationError):
        SubtitleSegmentIn(start=-0.1, end=1.0, text="x")


def test_subtitle_segment_out_tolerates_infinity_on_read():
    # Il modello di OUTPUT NON reimpone allow_inf_nan: rispecchia i dati a DB cosi'
    # come sono. L'irrigidimento riguarda solo l'input, quindi la lettura di righe
    # legacy non regredisce.
    out = SubtitleSegmentOut(id=1, idx=0, start=1.0, end=math.inf, text="x", words=None)
    assert math.isinf(out.end)


# --------------------------------------------------------------------------- #
# schemas: VideoPatch
# --------------------------------------------------------------------------- #
def test_videopatch_empty_is_valid_noop():
    p = VideoPatch()
    assert p.trim_start is None and p.subtitle_style is None


def test_videopatch_full_valid():
    p = VideoPatch(trim_start=1.0, trim_end=9.0, cuts=[{"start": 3, "end": 5}],
                   subtitle_style="tiktok_big", status="ready")
    assert p.trim_end == 9.0 and len(p.cuts) == 1


def test_videopatch_rejects_reversed_trim():
    with pytest.raises(ValidationError):
        VideoPatch(trim_start=5.0, trim_end=3.0)


def test_videopatch_rejects_equal_trim():
    with pytest.raises(ValidationError):
        VideoPatch(trim_start=5.0, trim_end=5.0)


def test_videopatch_only_trim_end_skips_coherence():
    # senza trim_start nella stessa richiesta la coerenza spetta al router
    p = VideoPatch(trim_end=1.0)
    assert p.trim_end == 1.0


def test_videopatch_clear_trim_end_bypasses_coherence():
    p = VideoPatch(trim_start=5.0, trim_end=3.0, clear_trim_end=True)
    assert p.clear_trim_end is True


def test_videopatch_rejects_negative_trim_end():
    with pytest.raises(ValidationError):
        VideoPatch(trim_end=-1.0)


def test_videopatch_rejects_infinite_trim():
    with pytest.raises(ValidationError):
        VideoPatch(trim_start=1.0, trim_end=math.inf)


def test_videopatch_rejects_unknown_style():
    with pytest.raises(ValidationError):
        VideoPatch(subtitle_style="boh")


def test_videopatch_accepts_none_style():
    assert VideoPatch(subtitle_style=None).subtitle_style is None


def test_videopatch_accepts_all_known_styles():
    for style_id in STYLES:
        assert VideoPatch(subtitle_style=style_id).subtitle_style == style_id


def test_videopatch_trim_start_beyond_duration_is_schema_valid():
    # 99 e' un float valido: il confronto con la durata reale spetta al router
    assert VideoPatch(trim_start=99.0).trim_start == 99.0


def test_videopatch_rejects_infinite_cut():
    with pytest.raises(ValidationError):
        VideoPatch(cuts=[{"start": 1, "end": math.inf}])


# --------------------------------------------------------------------------- #
# schemas: karaoke_color (VideoPatch / TemplateIn)
# --------------------------------------------------------------------------- #
def test_videopatch_karaoke_color_valid_and_normalized():
    # None = "non cambiare"; assente per default
    assert VideoPatch().karaoke_color is None
    assert VideoPatch(karaoke_color=None).karaoke_color is None
    # "#RRGGBB" e "RRGGBB" (senza cancelletto) -> normalizzati a "#RRGGBB" maiuscolo
    assert VideoPatch(karaoke_color="#ff0000").karaoke_color == "#FF0000"
    assert VideoPatch(karaoke_color="00ff00").karaoke_color == "#00FF00"


def test_videopatch_karaoke_color_rejects_invalid():
    for bad in ("#12345", "#GGGGGG", "red", "#1234567", "12 34 56"):
        with pytest.raises(ValidationError):
            VideoPatch(karaoke_color=bad)


def test_templatein_karaoke_color_valid_and_invalid():
    assert TemplateIn(name="F").karaoke_color is None
    assert TemplateIn(name="F", karaoke_color="#AbCdEf").karaoke_color == "#ABCDEF"
    with pytest.raises(ValidationError):
        TemplateIn(name="F", karaoke_color="nope")


# --------------------------------------------------------------------------- #
# schemas: TemplateIn
# --------------------------------------------------------------------------- #
def test_templatein_valid_and_default_style():
    # default aggiornato: si tiene solo il karaoke come stile predefinito
    assert TemplateIn(name="F").subtitle_style == "karaoke_word"
    assert TemplateIn(name="F", subtitle_style="classic_yellow").subtitle_style == "classic_yellow"
    # automazioni attive di default anche sul Format
    t = TemplateIn(name="F")
    assert t.intro_zoom and t.auto_silence and t.auto_retakes and t.auto_speedup


def test_templatein_rejects_empty_name():
    with pytest.raises(ValidationError):
        TemplateIn(name="")


def test_templatein_rejects_too_long_name():
    with pytest.raises(ValidationError):
        TemplateIn(name="x" * 81)


def test_templatein_rejects_infinite_cut():
    with pytest.raises(ValidationError):
        TemplateIn(name="F", cuts=[{"start": 1, "end": math.inf}])


def test_templatein_rejects_infinite_trim():
    with pytest.raises(ValidationError):
        TemplateIn(name="F", trim_start=math.inf)


def test_templatein_style_not_validated_at_schema():
    # Contratto voluto: lo stile del Format e' validato dal ROUTER, non dallo schema
    # (TemplateOut eredita da TemplateIn: un validator qui romperebbe la lettura di
    # template legacy con stile fuori-preset). Vedi routers/templates.py.
    assert TemplateIn(name="F", subtitle_style="stile-non-esistente").subtitle_style == "stile-non-esistente"
