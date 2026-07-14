"""QA: invarianti del piano di render (apply_speedups / map_time_plan /
remap_segments_detailed_plan) su app/services/timeline.py.

Modulo PURO: nessun I/O, nessun import dell'app, gira offline.
I casi base di apply_speedups (partizione di un keep, clamp su keep singolo,
speedup fuori dai keep, piano banale) sono gia' coperti da test_speedup.py e
NON vengono duplicati qui: questo file copre gli invarianti strutturali del
piano (non-sovrapposizione, contiguita', copertura) e i casi limite.
"""
import pytest

from app.services.timeline import (
    MIN_INTERVAL,
    apply_speedups,
    keeps_to_plan,
    map_time_plan,
    output_duration,
    plan_output_duration,
    remap_segments_detailed_plan,
)


def _assert_non_overlapping(plan):
    """Invariante fondamentale: i segmenti del piano non si sovrappongono mai
    (ogni istante della timeline originale viene renderizzato al piu' una volta)."""
    for (s1, e1, _), (s2, e2, _) in zip(plan, plan[1:]):
        assert e1 <= s2, (
            f"segmenti sovrapposti nel piano: ({s1}, {e1}) e ({s2}, {e2})"
        )


# --------------------------------------------------------------------------- #
# 1. TEST ROSSO — speedup sovrapposti producono un piano con segmenti sovrapposti
# --------------------------------------------------------------------------- #
# BUG CONFERMATO QA-01: apply_speedups (righe 124-131) non gestisce speedup
# sovrapposti: con keep (0,10) e speedups [(1,5,x2), (4,8,x3)] il piano risultante
# e' [(0,1,1),(1,5,2),(4,8,3),(8,10,1)] -> il tratto 4-5 compare in DUE segmenti
# e viene renderizzato due volte — vedi TEST_REPORT.md
def test_apply_speedups_overlapping_spans_must_not_overlap_in_plan():
    plan = apply_speedups(
        [(0.0, 10.0)],
        [{"start": 1.0, "end": 5.0, "factor": 2.0},
         {"start": 4.0, "end": 8.0, "factor": 3.0}],
    )
    # Comportamento CORRETTO atteso: nessuna coppia consecutiva sovrapposta.
    _assert_non_overlapping(plan)


# --------------------------------------------------------------------------- #
# 2. VERDI — invarianti del piano su speedup NON sovrapposti
# --------------------------------------------------------------------------- #
def test_disjoint_speedups_plan_is_contiguous_and_covers_keeps_exactly():
    keeps = [(0.0, 10.0), (12.0, 20.0)]
    plan = apply_speedups(
        keeps,
        [{"start": 1.0, "end": 3.0, "factor": 2.0},
         {"start": 5.0, "end": 7.0, "factor": 4.0},
         {"start": 13.0, "end": 15.0, "factor": 2.0}],
    )
    assert plan == [
        (0.0, 1.0, 1.0), (1.0, 3.0, 2.0), (3.0, 5.0, 1.0),
        (5.0, 7.0, 4.0), (7.0, 10.0, 1.0),
        (12.0, 13.0, 1.0), (13.0, 15.0, 2.0), (15.0, 20.0, 1.0),
    ]
    _assert_non_overlapping(plan)
    # copertura esatta: dentro ogni keep il piano e' contiguo, senza buchi
    # ne' sforamenti, e la somma dei tratti coincide con la durata dei keep.
    for ks, ke in keeps:
        segs = [(s, e) for s, e, _ in plan if ks <= s and e <= ke]
        assert segs[0][0] == ks and segs[-1][1] == ke
        for (_, e1), (s2, _) in zip(segs, segs[1:]):
            assert e1 == s2
    assert sum(e - s for s, e, _ in plan) == pytest.approx(output_duration(keeps))


def test_keeps_to_plan_duration_matches_output_duration():
    keeps = [(0.5, 4.25), (6.0, 9.9), (11.0, 30.0)]
    assert plan_output_duration(keeps_to_plan(keeps)) == pytest.approx(
        output_duration(keeps)
    )


def test_speedup_straddling_keep_border_is_clamped_on_both_keeps():
    # lo speedup (2,6) attraversa il taglio 3-5: viene clampato al bordo di
    # ENTRAMBI i keep, senza reintrodurre il tratto tagliato.
    plan = apply_speedups(
        [(0.0, 3.0), (5.0, 10.0)],
        [{"start": 2.0, "end": 6.0, "factor": 2.0}],
    )
    assert plan == [
        (0.0, 2.0, 1.0), (2.0, 3.0, 2.0),
        (5.0, 6.0, 2.0), (6.0, 10.0, 1.0),
    ]
    _assert_non_overlapping(plan)


def test_speedup_entirely_inside_cut_is_ignored():
    keeps = [(0.0, 3.0), (7.0, 10.0)]
    plan = apply_speedups(keeps, [{"start": 4.0, "end": 6.0, "factor": 3.0}])
    assert plan == keeps_to_plan(keeps)


def test_factor_leq_one_and_malformed_dicts_are_ignored_without_errors():
    keeps = [(0.0, 4.0)]
    bad_speedups = [
        {"start": 1.0, "end": 2.0, "factor": 1.0},    # factor == 1 -> no-op
        {"start": 1.0, "end": 2.0, "factor": 0.5},    # factor < 1 -> ignorato
        {"start": 1.0, "end": 2.0},                    # factor assente (default 1)
        {"end": 2.0, "factor": 3.0},                   # manca "start"
        {"start": 1.0, "factor": 3.0},                 # manca "end"
        {"start": "x", "end": 2.0, "factor": 3.0},     # valore non numerico
        {"start": None, "end": 2.0, "factor": 3.0},    # valore None
        {"start": 1.0, "end": 2.0, "factor": "boh"},   # factor non numerico
    ]
    plan = apply_speedups(keeps, bad_speedups)
    assert plan == keeps_to_plan(keeps)


def test_empty_keeps_yield_empty_plan():
    assert apply_speedups([], [{"start": 1.0, "end": 2.0, "factor": 2.0}]) == []
    assert apply_speedups([], None) == []


# --------------------------------------------------------------------------- #
# 3. VERDI — map_time_plan: monotonia, collasso nei buchi, coerenza col totale
# --------------------------------------------------------------------------- #
_PLAN_MULTI = apply_speedups(
    [(0.0, 4.0), (6.0, 12.0)],
    [{"start": 1.0, "end": 3.0, "factor": 2.0},
     {"start": 7.0, "end": 11.0, "factor": 4.0}],
)
# = [(0,1,1),(1,3,2),(3,4,1),(6,7,1),(7,11,4),(11,12,1)]


def test_map_time_plan_is_monotone_non_decreasing_on_grid():
    grid = [i * 0.25 for i in range(0, 53)]  # 0.0 .. 13.0 (oltre la fine)
    mapped = [map_time_plan(t, _PLAN_MULTI) for t in grid]
    for prev, cur in zip(mapped, mapped[1:]):
        assert cur >= prev, f"map_time_plan non monotona: {prev} -> {cur}"


def test_map_time_plan_points_inside_hole_collapse_on_edge():
    # il buco 4-6 (tratto tagliato) collassa sul bordo: stesso output del
    # bordo destro del primo blocco e del bordo sinistro del secondo.
    edge = map_time_plan(4.0, _PLAN_MULTI)
    assert edge == pytest.approx(3.0)  # 1 + 2/2 + 1
    for t in (4.5, 5.0, 5.9, 6.0):
        assert map_time_plan(t, _PLAN_MULTI) == pytest.approx(edge)


def test_map_time_plan_end_matches_plan_output_duration():
    total = plan_output_duration(_PLAN_MULTI)
    assert map_time_plan(12.0, _PLAN_MULTI) == pytest.approx(total)
    # anche oltre la fine del piano si resta inchiodati al totale
    assert map_time_plan(100.0, _PLAN_MULTI) == pytest.approx(total)


# --------------------------------------------------------------------------- #
# 4. VERDE CHARACTERIZATION — sliver iniziale scartato: piano NON contiguo
# --------------------------------------------------------------------------- #
def test_characterization_leading_sliver_creates_micro_hole():
    # QA CHARACTERIZATION: la docstring di apply_speedups promette un piano
    # "contiguo per costruzione", ma un tratto residuo < MIN_INTERVAL (qui
    # 0.0-0.02 prima dello speedup) viene scartato senza estendere il segmento
    # adiacente: il piano parte da 0.02 e NON copre il keep per intero
    # (micro-buco di 20ms). Comportamento attuale documentato, non corretto.
    speedup = {"start": 0.02, "end": 5.0, "factor": 2.0}
    assert speedup["start"] < MIN_INTERVAL  # e' proprio il caso sliver
    plan = apply_speedups([(0.0, 10.0)], [speedup])
    assert plan == [(0.02, 5.0, 2.0), (5.0, 10.0, 1.0)]
    assert plan[0][0] > 0.0  # il piano non parte dall'inizio del keep


# --------------------------------------------------------------------------- #
# 5. VERDI — remap_segments_detailed_plan
# --------------------------------------------------------------------------- #
_PLAN_WITH_CUT = keeps_to_plan([(0.0, 3.0), (5.0, 10.0)])  # taglio 3-5


def test_remap_detailed_plan_drops_words_inside_cut():
    seg = {
        "start": 2.0, "end": 6.0, "text": "a cavallo",
        "words": [[2.0, 2.8, "a"], [3.2, 4.8, "cavallo"], [5.2, 6.0, "fine"]],
    }
    out = remap_segments_detailed_plan([seg], _PLAN_WITH_CUT)
    assert len(out) == 1
    assert out[0]["start"] == pytest.approx(2.0)
    assert out[0]["end"] == pytest.approx(4.0)  # 6.0 -> 3 + (6-5)
    # la parola interamente nel taglio (3.2-4.8) collassa e sparisce
    assert out[0]["words"] == [[2.0, 2.8, "a"], [3.2, 4.0, "fine"]]


def test_remap_detailed_plan_words_none_when_none_survive():
    # il segmento sopravvive (0.5s in output) ma la sua unica parola collassa
    # nel taglio -> words deve tornare None (rendering non-karaoke)
    seg = {
        "start": 2.9, "end": 5.4, "text": "quasi",
        "words": [[3.1, 4.9, "quasi"]],
    }
    out = remap_segments_detailed_plan([seg], _PLAN_WITH_CUT)
    assert len(out) == 1
    assert out[0]["text"] == "quasi"
    assert out[0]["words"] is None


def test_remap_detailed_plan_filters_by_min_dur():
    segs = [
        # interamente nel taglio: collassa a durata 0 -> scartato
        {"start": 3.2, "end": 4.6, "text": "tagliato", "words": None},
        # sopravvive con durata 0.2 -> tenuto con min_dur di default (0.15),
        # scartato alzando min_dur a 0.3
        {"start": 1.0, "end": 1.2, "text": "corto", "words": None},
    ]
    out_default = remap_segments_detailed_plan(segs, _PLAN_WITH_CUT)
    assert [s["text"] for s in out_default] == ["corto"]
    out_strict = remap_segments_detailed_plan(segs, _PLAN_WITH_CUT, min_dur=0.3)
    assert out_strict == []
