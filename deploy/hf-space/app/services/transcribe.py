"""Trascrizione con faster-whisper (gratis, CPU-only di default).

Import e caricamento del modello sono lazy. Il modello resta in cache
di processo. Espone sia le PAROLE grezze con timestamp (per il rilevamento
di doppioni/ripartenze) sia le caption già chunkate.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from ..config import get_settings
from .captions import chunk_words, chunk_words_detailed

log = logging.getLogger(__name__)

_model = None
_model_key: tuple | None = None
_lock = threading.Lock()

Word = tuple[float, float, str]


def _get_model():
    global _model, _model_key
    s = get_settings()
    key = (s.whisper_model, s.whisper_device, s.whisper_compute, s.whisper_cpu_threads)
    with _lock:
        if _model is None or _model_key != key:
            from faster_whisper import WhisperModel  # import pesante: solo qui
            log.info("Carico modello Whisper %s (%s/%s)…", *key[:3])
            _model = WhisperModel(
                s.whisper_model,
                device=s.whisper_device,
                compute_type=s.whisper_compute,
                cpu_threads=s.whisper_cpu_threads,
            )
            _model_key = key
    return _model


def transcribe_words(
    path: str,
    duration: float,
    progress_cb: Callable[[float], None] | None = None,
) -> tuple[list[Word], list[Word]]:
    """Ritorna (parole_con_timestamp, segmenti_fallback_senza_parole)."""
    s = get_settings()
    model = _get_model()

    segments_iter, _info = model.transcribe(
        path,
        language=s.whisper_language or None,
        vad_filter=True,
        word_timestamps=True,
        beam_size=max(1, s.whisper_beam),
        condition_on_previous_text=False,  # meno allucinazioni su clip brevi
        initial_prompt=(s.whisper_prompt or None),  # vocabolario/brand per orientare l'ASR
    )

    words: list[Word] = []
    fallback: list[Word] = []
    for seg in segments_iter:
        if seg.words:
            for w in seg.words:
                words.append((float(w.start), float(w.end), w.word))
        elif seg.text.strip():
            fallback.append((float(seg.start), float(seg.end), seg.text.strip()))
        if progress_cb and duration > 0:
            progress_cb(min(float(seg.end) / duration, 0.99))
    return words, fallback


def captions_from_words(words: list[Word], fallback: list[Word]) -> list[Word]:
    """Chunka le parole in caption leggibili e aggiunge i segmenti fallback."""
    s = get_settings()
    captions = chunk_words(words, max_chars=s.sub_max_chars, max_gap=s.sub_max_gap)
    captions.extend(fallback)
    captions.sort(key=lambda c: c[0])
    return captions


def captions_with_words(words: list[Word], fallback: list[Word]) -> list[dict]:
    """Come captions_from_words, ma ogni caption conserva le sue parole.

    Ritorna dict {start, end, text, words} con words = [[start, end, "parola"], ...];
    per i segmenti fallback (Whisper senza timestamp di parola) words è None.
    """
    s = get_settings()
    captions = chunk_words_detailed(words, max_chars=s.sub_max_chars, max_gap=s.sub_max_gap)
    captions.extend(
        {"start": f_start, "end": f_end, "text": f_text, "words": None}
        for f_start, f_end, f_text in fallback
    )
    captions.sort(key=lambda c: c["start"])
    return captions


def transcribe_to_captions(
    path: str,
    duration: float,
    progress_cb: Callable[[float], None] | None = None,
) -> list[Word]:
    """Compat: trascrive e ritorna direttamente le caption."""
    words, fallback = transcribe_words(path, duration, progress_cb)
    return captions_from_words(words, fallback)
