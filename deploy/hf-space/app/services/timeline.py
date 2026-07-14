"""Funzioni PURE sulla timeline: nessun I/O, unit-testate.

Convenzioni:
- i `cuts` sono intervalli DA RIMUOVERE, espressi sulla timeline originale;
- i `keeps` sono gli intervalli DA TENERE che ne derivano (trim + cuts);
- i sottotitoli vivono sulla timeline originale e vengono rimappati
  sulla timeline di output solo al momento dell'export.
"""
from __future__ import annotations

MIN_INTERVAL = 0.05  # intervalli più corti di così vengono scartati


def normalize_cuts(cuts: list[dict], duration: float) -> list[tuple[float, float]]:
    """Clampa nel range del video, ordina e fonde le sovrapposizioni."""
    cleaned: list[tuple[float, float]] = []
    for c in cuts or []:
        s = max(0.0, float(c["start"]))
        e = min(float(duration), float(c["end"]))
        if e - s >= MIN_INTERVAL:
            cleaned.append((s, e))
    cleaned.sort()
    merged: list[tuple[float, float]] = []
    for s, e in cleaned:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def keep_intervals(
    duration: float,
    trim_start: float = 0.0,
    trim_end: float | None = None,
    cuts: list[dict] | None = None,
) -> list[tuple[float, float]]:
    """Complemento dei cuts all'interno della finestra di trim."""
    start = max(0.0, min(trim_start, duration))
    end = duration if trim_end is None else max(0.0, min(trim_end, duration))
    if end - start < MIN_INTERVAL:
        raise ValueError("Intervallo di trim vuoto: controlla inizio/fine")

    keeps: list[tuple[float, float]] = []
    cursor = start
    for cs, ce in normalize_cuts(cuts or [], duration):
        if ce <= start or cs >= end:
            continue
        cs, ce = max(cs, start), min(ce, end)
        if cs - cursor >= MIN_INTERVAL:
            keeps.append((cursor, cs))
        cursor = max(cursor, ce)
    if end - cursor >= MIN_INTERVAL:
        keeps.append((cursor, end))
    if not keeps:
        raise ValueError("I tagli rimuovono tutto il video")
    return keeps


def output_duration(keeps: list[tuple[float, float]]) -> float:
    return sum(e - s for s, e in keeps)


def map_time(t: float, keeps: list[tuple[float, float]]) -> float:
    """Tempo originale -> tempo di output. I punti dentro un taglio collassano
    sul bordo del keep successivo."""
    cum = 0.0
    for s, e in keeps:
        if t < s:
            return cum
        if t <= e:
            return cum + (t - s)
        cum += e - s
    return cum


def remap_segments(
    segments: list[tuple[float, float, str]],
    keeps: list[tuple[float, float]],
    min_dur: float = 0.15,
) -> list[tuple[float, float, str]]:
    """Rimappa i sottotitoli sulla timeline di output.

    Un segmento interamente dentro un taglio sparisce; uno a cavallo di un
    taglio si accorcia in modo naturale (le parti tenute diventano contigue).
    """
    out: list[tuple[float, float, str]] = []
    for start, end, text in segments:
        ns, ne = map_time(start, keeps), map_time(end, keeps)
        if ne - ns >= min_dur and text.strip():
            out.append((round(ns, 3), round(ne, 3), text))
    return out


# --------------------------------------------------------------------------- #
# PIANO DI RENDER con velocita' per-segmento (per velocizzare i silenzi lunghi).
# Un "plan" e' una lista di (start, end, speed) sulla timeline ORIGINALE: speed=1
# tempo reale, speed>1 tratto accelerato. Riduce a keep_intervals quando speed==1.
# --------------------------------------------------------------------------- #
Plan = list[tuple[float, float, float]]


def apply_speedups(
    keeps: list[tuple[float, float]],
    speedups: list[dict] | None,
    min_seg: float = MIN_INTERVAL,
) -> Plan:
    """Partiziona i keep (speed 1) inserendo i tratti da velocizzare che li
    intersecano. I speedup fuori dai keep (o dentro un taglio) vengono ignorati.
    Ritorna un piano [(start, end, speed)] contiguo per costruzione."""
    spans: list[tuple[float, float, float]] = []
    for sp in speedups or []:
        try:
            a, b, f = float(sp["start"]), float(sp["end"]), float(sp.get("factor", 1.0))
        except (TypeError, ValueError, KeyError):
            continue
        if b - a >= min_seg and f > 1.0:
            spans.append((a, b, f))
    spans.sort()

    plan: Plan = []
    for ks, ke in keeps:
        cursor = ks
        for a, b, f in spans:
            # a2 clampato anche su cursor: span di speedup sovrapposti (o che
            # sconfinano nel precedente) non devono mai produrre segmenti di
            # piano sovrapposti -> ogni istante renderizzato al più una volta.
            a2, b2 = max(ks, cursor, a), min(ke, b)
            if b2 - a2 < min_seg or b2 <= cursor:
                continue
            if a2 - cursor >= min_seg:
                plan.append((cursor, a2, 1.0))
            plan.append((a2, b2, f))
            cursor = b2
        if ke - cursor >= min_seg:
            plan.append((cursor, ke, 1.0))
    return plan


def keeps_to_plan(keeps: list[tuple[float, float]]) -> Plan:
    """Piano banale a velocita' 1 (quando non ci sono velocizzazioni)."""
    return [(s, e, 1.0) for s, e in keeps]


def plan_output_duration(plan: Plan) -> float:
    return sum((e - s) / spd for s, e, spd in plan)


def map_time_plan(t: float, plan: Plan) -> float:
    """Tempo originale -> tempo di output, tenendo conto della velocita' di ogni
    segmento. I punti dentro un buco (tra due segmenti) collassano sul bordo."""
    cum = 0.0
    for s, e, spd in plan:
        if t < s:
            return cum
        if t <= e:
            return cum + (t - s) / spd
        cum += (e - s) / spd
    return cum


def remap_segments_detailed_plan(
    segments: list[dict],
    plan: Plan,
    min_dur: float = 0.15,
    min_word: float = MIN_INTERVAL,
) -> list[dict]:
    """Come remap_segments_detailed ma su un piano con velocita' per-segmento.
    (I sottotitoli stanno sul parlato = tratti a velocita' 1, ma la mappatura
    tiene comunque conto dei tratti accelerati che li precedono.)"""
    out: list[dict] = []
    for seg in segments:
        text = seg["text"]
        ns = map_time_plan(float(seg["start"]), plan)
        ne = map_time_plan(float(seg["end"]), plan)
        if ne - ns < min_dur or not text.strip():
            continue
        words = None
        if seg.get("words"):
            words = []
            for w_start, w_end, w_text in seg["words"]:
                ws, we = map_time_plan(float(w_start), plan), map_time_plan(float(w_end), plan)
                if we - ws >= min_word:
                    words.append([round(ws, 3), round(we, 3), w_text])
            words = words or None
        out.append({"start": round(ns, 3), "end": round(ne, 3), "text": text, "words": words})
    return out


def remap_segments_detailed(
    segments: list[dict],
    keeps: list[tuple[float, float]],
    min_dur: float = 0.15,
    min_word: float = MIN_INTERVAL,
) -> list[dict]:
    """Come remap_segments, ma su segmenti-dict {start, end, text, words}.

    Le parole (se presenti) vengono rimappate una a una con map_time; quelle
    che collassano dentro un taglio (durata < min_word) vengono scartate.
    Se non sopravvive nessuna parola, words torna None (rendering normale).
    """
    out: list[dict] = []
    for seg in segments:
        text = seg["text"]
        ns, ne = map_time(float(seg["start"]), keeps), map_time(float(seg["end"]), keeps)
        if ne - ns < min_dur or not text.strip():
            continue
        words = None
        if seg.get("words"):
            words = []
            for w_start, w_end, w_text in seg["words"]:
                ws, we = map_time(float(w_start), keeps), map_time(float(w_end), keeps)
                if we - ws >= min_word:
                    words.append([round(ws, 3), round(we, 3), w_text])
            words = words or None
        out.append({"start": round(ns, 3), "end": round(ne, 3), "text": text, "words": words})
    return out
