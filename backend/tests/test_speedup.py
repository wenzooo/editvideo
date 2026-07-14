"""Velocizza silenzi lunghi: piano di render con velocita' per-segmento +
classificazione dei silenzi (taglio vs velocizzazione). Funzioni pure, offline."""
import pytest

from app.services.silence import silences_to_cuts_and_speedups
from app.services.timeline import (
    apply_speedups,
    map_time_plan,
    plan_output_duration,
    remap_segments_detailed_plan,
)


# --------------------------------------------------------------- apply_speedups
def test_apply_speedups_partitions_keep():
    plan = apply_speedups([(0.0, 10.0)], [{"start": 3.0, "end": 7.0, "factor": 4.0}])
    assert plan == [(0.0, 3.0, 1.0), (3.0, 7.0, 4.0), (7.0, 10.0, 1.0)]


def test_apply_speedups_clips_to_keep():
    # lo speedup sfora il keep: viene tagliato al bordo del keep
    plan = apply_speedups([(0.0, 5.0)], [{"start": 2.0, "end": 8.0, "factor": 4.0}])
    assert plan == [(0.0, 2.0, 1.0), (2.0, 5.0, 4.0)]


def test_apply_speedups_ignores_span_outside_keeps():
    plan = apply_speedups([(0.0, 2.0)], [{"start": 5.0, "end": 9.0, "factor": 4.0}])
    assert plan == [(0.0, 2.0, 1.0)]


def test_no_speedups_is_trivial_plan():
    assert apply_speedups([(0.0, 4.0), (6.0, 8.0)], []) == [(0.0, 4.0, 1.0), (6.0, 8.0, 1.0)]


# --------------------------------------------------------------- durata / mappa
def test_plan_output_duration_compresses_speedup():
    plan = [(0.0, 3.0, 1.0), (3.0, 7.0, 4.0), (7.0, 10.0, 1.0)]
    # 3 + (4/4=1) + 3 = 7 (invece di 10)
    assert plan_output_duration(plan) == pytest.approx(7.0)


def test_map_time_plan_accounts_for_speed():
    plan = [(0.0, 3.0, 1.0), (3.0, 7.0, 4.0), (7.0, 10.0, 1.0)]
    assert map_time_plan(0.0, plan) == pytest.approx(0.0)
    assert map_time_plan(3.0, plan) == pytest.approx(3.0)
    assert map_time_plan(5.0, plan) == pytest.approx(3.5)   # meta' del tratto 4x
    assert map_time_plan(7.0, plan) == pytest.approx(4.0)
    assert map_time_plan(10.0, plan) == pytest.approx(7.0)


def test_remap_caption_after_speedup():
    plan = apply_speedups([(0.0, 10.0)], [{"start": 3.0, "end": 7.0, "factor": 4.0}])
    seg = {"start": 8.0, "end": 9.0, "text": "dopo il silenzio",
           "words": [[8.0, 8.5, "dopo"], [8.5, 9.0, "silenzio"]]}
    out = remap_segments_detailed_plan([seg], plan)
    assert len(out) == 1
    assert out[0]["start"] == pytest.approx(5.0)   # 3 + 1 + (8-7)
    assert out[0]["end"] == pytest.approx(6.0)
    assert [w[2] for w in out[0]["words"]] == ["dopo", "silenzio"]


# --------------------------------------------------- classificazione dei silenzi
def test_long_silence_becomes_speedup_not_cut():
    cuts, sp = silences_to_cuts_and_speedups(
        [(10.0, 16.0)], duration=30.0, speedup_min=2.5, speedup_factor=4.0, speedup_edge=0.15)
    assert cuts == []
    assert len(sp) == 1
    assert sp[0]["start"] == pytest.approx(10.15)
    assert sp[0]["end"] == pytest.approx(15.85)
    assert sp[0]["factor"] == 4.0


def test_short_silence_is_cut_not_speedup():
    cuts, sp = silences_to_cuts_and_speedups(
        [(3.0, 3.8)], duration=30.0, leave=0.30, speedup_min=2.5)
    assert sp == []
    assert len(cuts) == 1


def test_edge_silence_is_cut_even_if_long():
    # silenzio iniziale lungo: si taglia (dead air), non si velocizza
    cuts, sp = silences_to_cuts_and_speedups(
        [(0.0, 6.0)], duration=30.0, speedup_min=2.5)
    assert sp == []
    assert len(cuts) == 1


def test_speedup_disabled_falls_back_to_cut():
    cuts, sp = silences_to_cuts_and_speedups(
        [(10.0, 16.0)], duration=30.0, do_speedup=False, speedup_min=2.5)
    assert sp == []
    assert len(cuts) == 1
