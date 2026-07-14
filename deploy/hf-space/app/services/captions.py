"""Chunking: dalle parole con timestamp (Whisper) a caption leggibili.

Regole (tarate su caption verticali TikTok/Reels):
- max N caratteri per caption (default 42);
- una pausa nel parlato > max_gap apre una nuova caption;
- la punteggiatura forte (. ! ? …) chiude la caption corrente.
Funzioni pure, unit-testate.
"""
from __future__ import annotations

_STRONG_PUNCT = (".", "!", "?", "…")


def chunk_words_detailed(
    words: list[tuple[float, float, str]],
    max_chars: int = 42,
    max_gap: float = 0.8,
) -> list[dict]:
    """Chunka le parole in caption conservando, per ogni caption, le sue parole.

    Ritorna dict {start, end, text, words} con words = [[w_start, w_end, testo], ...]
    (timestamp arrotondati a 3 decimali). Stesse regole di flush di chunk_words:
    max_chars, pausa > max_gap, punteggiatura forte.
    """
    chunks: list[dict] = []
    cur: list[tuple[float, float, str]] = []
    cur_start = 0.0
    cur_end = 0.0

    def flush():
        nonlocal cur
        text = " ".join(t.strip() for _ws, _we, t in cur if t.strip()).strip()
        if text:
            chunks.append({
                "start": round(cur_start, 3),
                "end": round(max(cur_end, cur_start + 0.2), 3),
                "text": text,
                "words": [[round(ws, 3), round(we, 3), t] for ws, we, t in cur],
            })
        cur = []

    for w_start, w_end, w_text in words:
        w_text = w_text.strip()
        if not w_text:
            continue
        if cur:
            candidate_len = len(" ".join(t for _ws, _we, t in cur)) + 1 + len(w_text)
            if candidate_len > max_chars or (w_start - cur_end) > max_gap:
                flush()
        if not cur:
            cur_start = w_start
        cur.append((w_start, w_end, w_text))
        cur_end = w_end
        if w_text.endswith(_STRONG_PUNCT):
            flush()

    flush()
    # Nessuna caption deve sconfinare oltre l'inizio della successiva: il padding
    # minimo (+0.2) applicato in flush() poteva far sovrapporre due Dialogue nel
    # file .ass (testo impilato a schermo). Si clampa a valle, quando lo start
    # della caption seguente è noto.
    for i in range(len(chunks) - 1):
        nxt_start = chunks[i + 1]["start"]
        if chunks[i]["end"] > nxt_start:
            chunks[i]["end"] = nxt_start
    return chunks


def chunk_words(
    words: list[tuple[float, float, str]],
    max_chars: int = 42,
    max_gap: float = 0.8,
) -> list[tuple[float, float, str]]:
    """Compat: come chunk_words_detailed, ma ritorna tuple (start, end, text)."""
    return [(c["start"], c["end"], c["text"])
            for c in chunk_words_detailed(words, max_chars, max_gap)]
