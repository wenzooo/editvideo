"""Rilevamento di doppioni / ripartenze nel parlato.

Quando chi parla sbaglia, di solito si ferma e RIPETE la frase dall'inizio:

    "quindi noi andiamo a... quindi noi andiamo a creare il progetto"
     └──── primo tentativo ───┘└──────── ripresa buona ─────────────┘

L'algoritmo lavora sui word-timestamps di Whisper.

Due casi, gestiti da `detect_all_retake_cuts`:

1. Ripartenze BREVI (`detect_retake_cuts`): una sequenza di almeno `min_match`
   parole (normalizzate) si ripete entro `window_s` secondi -> si taglia dal
   primo tentativo alla ripresa (si tiene l'ultima).

2. Riprese dell'INTERO discorso (`detect_full_restart_cut`): il soggetto
   ricomincia da capo (anche piu' volte). L'INCIPIT del discorso ricompare piu'
   avanti. Detection robusta (tollerante alle sbavature dell'ASR) MA senza il
   "mix" apertura+coda: si trova l'ultima occorrenza dell'incipit e si taglia in
   BLOCCO da inizio discorso a quell'occorrenza. La parte tenuta e' quindi SEMPRE
   la coda contigua (l'ultima ripresa completa), mai pezzi cuciti insieme.

Funzioni pure, unit-testate: l'unica dipendenza e' il puro helper
`timeline.normalize_cuts` (import locale), nessun aggancio a fastapi/db/whisper.
"""
from __future__ import annotations

import re
import unicodedata

_PUNCT_RE = re.compile(r"[^\w]+", re.UNICODE)

Word = tuple[float, float, str]


def _norm(word: str) -> str:
    w = unicodedata.normalize("NFKD", word.strip().lower())
    w = "".join(c for c in w if not unicodedata.combining(c))
    return _PUNCT_RE.sub("", w)


def _norm_words(words: list[Word]) -> list[Word]:
    """Parole normalizzate NON vuote: [(start, end, norm)]."""
    return [(s, e, _norm(t)) for s, e, t in words if _norm(t)]


def _seq_match(a: list[str], b: list[str], max_mismatch: int) -> bool:
    """True se due sequenze di pari lunghezza coincidono a meno di max_mismatch
    parole diverse (tolleranza alle sbavature della trascrizione)."""
    return sum(1 for x, y in zip(a, b) if x != y) <= max_mismatch


def detect_retake_cuts(
    words: list[Word],
    min_match: int = 3,
    window_s: float = 10.0,
    max_cut_s: float = 20.0,
    pad: float = 0.06,
) -> list[dict]:
    """Ripartenze BREVI: [{start,end}] dei tentativi abortiti (timeline originale).

    Una sequenza di >= min_match parole che si ripete entro window_s: taglia
    dall'inizio del 1o tentativo all'inizio della ripresa, tenendo l'ultima.
    """
    W = _norm_words(words)
    n = len(W)
    cuts: list[dict] = []
    i = 0
    while i < n - min_match:
        found = None
        j = i + 1
        # la ripresa deve iniziare entro window_s dalla fine della prima parola
        while j <= n - min_match and (W[j][0] - W[i][1]) <= window_s:
            k = 0
            while (i + k < j and j + k < n and k < min_match
                   and W[i + k][2] == W[j + k][2]):
                k += 1
            if k >= min_match and (W[j][0] - W[i][0]) <= max_cut_s:
                found = j
                break
            j += 1
        if found is not None:
            cs = max(0.0, W[i][0] - pad)
            ce = max(cs, W[found][0] - pad)
            if ce - cs >= 0.3:
                cuts.append({"start": round(cs, 3), "end": round(ce, 3)})
            i = found  # riparti dalla ripresa buona
        else:
            i += 1
    return cuts


def _restart_from_anchor(
    W: list[Word], anchor: int, min_match_full: int, pad: float,
    max_cut_full: float, opening_tol: int, lead_in: float = 0.15,
) -> tuple[dict, float] | None:
    """Prova a rilevare la ripresa piena usando come incipit le parole a partire
    da `anchor`. Ritorna (cut, tail_start_time) oppure None."""
    n = len(W)
    if anchor + min_match_full > n:
        return None
    opening = [W[anchor + k][2] for k in range(min_match_full)]
    occ = [anchor]
    j = anchor + min_match_full
    while j <= n - min_match_full:
        seg = [W[j + k][2] for k in range(min_match_full)]
        if _seq_match(seg, opening, opening_tol):
            occ.append(j)
            j += min_match_full  # niente match sovrapposti
        else:
            j += 1
    if len(occ) < 2:
        return None  # l'incipit non ricompare -> nessuna ripresa piena
    last = occ[-1]
    if last == 0:
        return None
    # si taglia SEMPRE da inizio discorso all'ultima ripresa: coda contigua, no mix.
    # Lascia un piccolo "respiro" prima della ripresa tenuta MA solo se prima c'e'
    # una pausa (mai tagliare dentro il parlato del tentativo abortito): entrata
    # piu' morbida, meno "tagliato di brutto".
    gap = W[last][0] - W[last - 1][1]
    lead = min(max(0.0, lead_in), max(0.0, gap - 0.03))
    cs = max(0.0, W[0][0] - pad)
    ce = max(cs, W[last][0] - lead)
    if ce - cs < 0.3 or (ce - cs) > max_cut_full:
        return None
    return {"start": round(cs, 3), "end": round(ce, 3)}, ce


def detect_full_restart_cut(
    words: list[Word],
    *,
    min_match_full: int = 5,
    restart_gap: float = 0.35,
    pad: float = 0.06,
    max_cut_full: float = 600.0,
    opening_tol: int = 1,
    lead_in: float = 0.15,
) -> tuple[list[dict], float]:
    """Riprese dell'INTERO discorso: robusto ma senza cuciture sbagliate.

    Cerca l'INCIPIT del discorso (`min_match_full` parole, con tolleranza
    `opening_tol` a differenze ASR) ripetuto piu' avanti, OVUNQUE (non serve una
    pausa netta): questo restituisce la sensibilita' persa nella versione troppo
    severa. L'anti-"mix" e' garantito dal fatto che il taglio va SEMPRE da inizio
    discorso all'ULTIMA occorrenza dell'incipit -> la parte tenuta e' la coda
    contigua (l'ultima ripresa completa), mai pezzi cuciti.

    Robusto anche a un falso-avvio molto corto in testa: se l'incipit assoluto non
    ricompare, riprova ancorando l'incipit ai primi confini di pausa
    (gap >= restart_gap).

    Ritorna (cuts, tail_start_time): cuts = [] o [{start,end}] del blocco abortito;
    tail_start_time = istante d'inizio della ripresa buona (0 se nessun riavvio).
    """
    W = _norm_words(words)
    n = len(W)
    if n < min_match_full * 2:
        return [], 0.0

    # ancore: inizio assoluto + i primi confini di pausa (gestisce il falso-avvio)
    anchors = [0] + [j for j in range(1, min(n, 15))
                     if (W[j][0] - W[j - 1][1]) >= restart_gap]
    for a in anchors:
        r = _restart_from_anchor(W, a, min_match_full, pad, max_cut_full, opening_tol, lead_in)
        if r is not None:
            return [r[0]], r[1]
    return [], 0.0


def detect_all_retake_cuts(
    words: list[Word],
    *,
    min_match: int = 3,
    window_s: float = 10.0,
    max_cut_s: float = 20.0,
    min_match_full: int = 5,
    window_full: float = 180.0,
    max_cut_full: float = 300.0,
    restart_gap: float = 0.35,
    opening_tol: int = 1,
    pad: float = 0.06,
) -> list[dict]:
    """Ripartenze BREVI (locali) + riprese dell'INTERO discorso (robuste, no mix).

    1. Full restart (`detect_full_restart_cut`): tiene solo l'ultima ripresa
       completa, coda contigua.
    2. Locale (`detect_retake_cuts`): stumble brevi ma solo DENTRO la ripresa
       tenuta, per non ri-tagliare i tentativi gia' rimossi dal full.

    I due set sono disgiunti per costruzione (full sta prima di tail_start_time,
    local dopo) e vengono uniti + normalizzati. `window_full` e' accettato per
    compat coi chiamanti (la detection full non usa piu' una finestra rigida).
    """
    from .timeline import normalize_cuts

    full, tail_start = detect_full_restart_cut(
        words,
        min_match_full=min_match_full,
        restart_gap=restart_gap,
        pad=pad,
        max_cut_full=max_cut_full,
        opening_tol=opening_tol,
    )
    tail_words = [w for w in words if w[0] >= tail_start] if tail_start else words
    local = detect_retake_cuts(
        tail_words, min_match=min_match, window_s=window_s,
        max_cut_s=max_cut_s, pad=pad)

    combined = full + local
    if not combined:
        return []
    total = max((e for _s, e, _t in words), default=0.0)
    merged = normalize_cuts(combined, total)
    return [{"start": round(s, 3), "end": round(e, 3)} for s, e in merged]


def filter_words_outside_cuts(
    words: list[Word],
    cuts: list[dict],
) -> list[Word]:
    """Toglie le parole che cadono dentro i tagli (per generare caption pulite)."""
    if not cuts:
        return words
    kept = []
    for s, e, t in words:
        mid = (s + e) / 2
        if not any(c["start"] <= mid <= c["end"] for c in cuts):
            kept.append((s, e, t))
    return kept
