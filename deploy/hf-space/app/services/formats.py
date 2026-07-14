"""Applicazione di un Format (template di editing) a un video.

Il template è definito su una struttura-tipo: trim iniziale assoluto,
coda tagliata in secondi DALLA FINE (robusta a piccole differenze di durata)
e tagli interni assoluti. I valori vengono clampati sulla durata reale;
se il risultato non lascerebbe nulla da esportare, il video resta intonso.
"""
from __future__ import annotations

from .timeline import keep_intervals


def apply_template(video, template) -> bool:
    """Muta il video (senza commit). Ritorna False se il format non è
    applicabile a questa durata (video lasciato con edit di default)."""
    d = float(video.duration or 0)
    if d <= 0:
        return False

    ts = float(template.trim_start or 0)
    if ts >= d - 0.5:
        ts = 0.0

    te = None
    tail = float(template.tail_trim or 0)
    if tail > 0:
        cand = d - tail
        te = cand if cand > ts + 0.5 else None

    cuts = []
    for c in template.cuts or []:
        s, e = max(0.0, float(c["start"])), min(d, float(c["end"]))
        if s < d and e - s >= 0.05:
            cuts.append({"start": round(s, 3), "end": round(e, 3)})

    try:
        keep_intervals(d, ts, te, cuts)
    except ValueError:
        return False

    video.trim_start = round(ts, 3)
    video.trim_end = None if te is None else round(te, 3)
    video.cuts = cuts
    video.subtitle_style = template.subtitle_style
    video.karaoke_color = getattr(template, "karaoke_color", None)
    if getattr(template, "sub_pos", None) is not None:
        video.sub_pos = float(template.sub_pos)
    if getattr(template, "sub_scale", None) is not None:
        video.sub_scale = float(template.sub_scale)
    video.intro_zoom = bool(getattr(template, "intro_zoom", True))
    video.auto_silence = bool(getattr(template, "auto_silence", True))
    video.auto_retakes = bool(getattr(template, "auto_retakes", True))
    video.auto_speedup = bool(getattr(template, "auto_speedup", True))
    video.auto_export = bool(getattr(template, "auto_export", False))
    return True
