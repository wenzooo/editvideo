"""Rilevamento dei silenzi (ffmpeg silencedetect) -> tagli precisi.

Strategia: si rilevano anche le pause brevi (>= min_dur, default 0.4s) e
di ogni pausa si taglia il CENTRO, lasciando un respiro residuo fisso
(`leave`, default 0.24s) diviso tra i due lati. Così il ritmo resta naturale
ma senza tempi morti. Le pause che toccano inizio/fine video vengono
tagliate a filo del parlato (respiro solo sul lato interno).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_START_RE = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_END_RE = re.compile(r"silence_end:\s*(-?[0-9.]+)")


def detect_silences(
    path: str | Path,
    noise_db: float = -35.0,
    min_dur: float = 0.4,
) -> list[tuple[float, float | None]]:
    """Ritorna [(inizio, fine), ...]; fine=None se il file termina in silenzio.

    `-vn` è essenziale: senza, il muxer `null` mappa anche lo stream video e
    ffmpeg decodifica l'intero H.264 1080x1920 solo per buttarlo, sprecando
    secondi di CPU per un filtro puramente audio. Con `-vn` il video non viene
    nemmeno decodificato (misurato: ~-80% di tempo/CPU su questo comando).
    """
    cmd = ["ffmpeg", "-i", str(path), "-vn",
           "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
           "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        # ffmpeg fallito (file corrotto, binario assente, arg errati): NON
        # ritornare [] in silenzio — sarebbe indistinguibile da "nessuna pausa".
        # Il chiamante (worker.run_transcribe) logga e prosegue senza tagli.
        tail = (proc.stderr or "").strip()[-300:]
        raise RuntimeError(
            f"ffmpeg silencedetect fallito (returncode={proc.returncode}): {tail}"
        )
    out = proc.stderr
    starts = [float(x) for x in _START_RE.findall(out)]
    ends = [float(x) for x in _END_RE.findall(out)]
    silences: list[tuple[float, float | None]] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        silences.append((max(0.0, s), e))
    return silences


def silences_to_cuts(
    silences: list[tuple[float, float | None]],
    duration: float,
    leave: float = 0.24,
    min_cut: float = 0.12,
) -> list[dict]:
    """Converte le pause in tagli lasciando `leave` secondi di respiro residuo.

    Pausa interna  [s,e]  -> taglio [s + leave/2, e - leave/2]
    Pausa iniziale [0,e]  -> taglio [0, e - leave/2]   (a filo del bordo)
    Pausa finale   [s,fine]-> taglio [s + leave/2, fine]
    """
    half = max(0.0, leave) / 2
    cuts: list[dict] = []
    for s, e in silences:
        if e is None:
            e = duration
        at_start = s <= 0.15
        at_end = e >= duration - 0.15
        cs = 0.0 if at_start else s + half
        ce = duration if at_end else e - half
        cs, ce = max(0.0, cs), min(duration, ce)
        if ce - cs >= min_cut:
            cuts.append({"start": round(cs, 3), "end": round(ce, 3)})
    return cuts


def auto_cuts_for(path: str | Path, duration: float,
                  noise_db: float = -35.0, min_dur: float = 0.4,
                  leave: float = 0.24) -> list[dict]:
    return silences_to_cuts(detect_silences(path, noise_db, min_dur),
                            duration, leave=leave)


def silences_to_cuts_and_speedups(
    silences: list[tuple[float, float | None]],
    duration: float,
    *,
    leave: float = 0.24,
    min_cut: float = 0.12,
    do_cut: bool = True,
    do_speedup: bool = True,
    speedup_min: float = 2.5,
    speedup_factor: float = 4.0,
    speedup_edge: float = 0.15,
) -> tuple[list[dict], list[dict]]:
    """Classifica ogni silenzio in TAGLIO o VELOCIZZAZIONE.

    - Silenzio lungo (>= speedup_min) NON ai bordi e con do_speedup: si tiene ma si
      accelera il centro -> speedup {start,end,factor}, lasciando `speedup_edge` s a
      velocita' 1 ai due lati (ingresso/uscita morbidi). Nessun taglio su di esso.
    - Altrimenti, con do_cut: taglio del centro lasciando `leave` s di respiro
      (identico a silences_to_cuts). I silenzi a inizio/fine si tagliano a filo.

    Ritorna (cuts, speedups), entrambi sulla timeline ORIGINALE.
    """
    half = max(0.0, leave) / 2
    cuts: list[dict] = []
    speedups: list[dict] = []
    for s, e in silences:
        if e is None:
            e = duration
        at_start = s <= 0.15
        at_end = e >= duration - 0.15
        gap = e - s
        if (do_speedup and gap >= speedup_min and not at_start and not at_end
                and speedup_factor > 1.0):
            a = s + max(0.0, speedup_edge)
            b = e - max(0.0, speedup_edge)
            if b - a >= 0.3:
                speedups.append({"start": round(a, 3), "end": round(b, 3),
                                 "factor": round(float(speedup_factor), 3)})
                continue  # velocizzato: non lo si taglia
        if do_cut:
            cs = 0.0 if at_start else s + half
            ce = duration if at_end else e - half
            cs, ce = max(0.0, cs), min(duration, ce)
            if ce - cs >= min_cut:
                cuts.append({"start": round(cs, 3), "end": round(ce, 3)})
    return cuts, speedups
