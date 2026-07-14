"""Precisione del taglia-doppioni full-restart (ancoraggio incipit + pausa).

Verifica il difetto segnalato: il riavvio dell'intero discorso NON deve cucire
l'apertura del primo tentativo con la coda del secondo ("mix"); deve tenere una
sola ripresa completa e coerente.
"""
from app.services.retakes import (
    detect_all_retake_cuts,
    detect_full_restart_cut,
    detect_retake_cuts,
    filter_words_outside_cuts,
)


def _mk(seq, t0=0.0, dt=0.3, wdur=0.25):
    """Costruisce word-timestamps da una lista di parole. Ritorna (words, t_next)."""
    words, t = [], t0
    for w in seq:
        words.append((round(t, 3), round(t + wdur, 3), w))
        t += dt
    return words, t


def _kept_text(words, cuts):
    return " ".join(w[2] for w in filter_words_outside_cuts(words, cuts))


def test_full_restart_two_takes_keeps_last_no_mix():
    take1, t1 = _mk("ciao a tutti oggi vi parlo".split(), 0.0)
    take2, _ = _mk("ciao a tutti oggi vi parlo di reti".split(), t1 + 1.0)  # pausa
    words = take1 + take2
    cuts = detect_all_retake_cuts(words, min_match_full=4, restart_gap=0.6)
    # tiene SOLO l'ultima ripresa, contigua e intera: nessun mix apertura+coda
    assert _kept_text(words, cuts) == "ciao a tutti oggi vi parlo di reti"


def test_full_restart_three_takes_keeps_only_last():
    t = 0.0
    take1, t = _mk("questo e il mio discorso sul progetto".split(), t)
    take2, t = _mk("questo e il mio discorso sul progetto nuovo".split(), t + 1.0)
    take3, _ = _mk("questo e il mio discorso sul progetto finale".split(), t + 1.0)
    words = take1 + take2 + take3
    cuts = detect_all_retake_cuts(words, min_match_full=4, restart_gap=0.6)
    assert _kept_text(words, cuts) == "questo e il mio discorso sul progetto finale"


def test_mid_phrase_repeat_is_not_a_full_restart():
    # frase comune ripetuta a META' discorso ("oggi parlo di X ... oggi parlo di
    # Y") NON deve innescare un taglio: col default (incipit 5 parole, tolleranza
    # 1) le due aperture differiscono di 2 parole -> nessuna ripresa, niente "mix".
    words, _ = _mk("oggi parlo di reti neurali e oggi parlo di altro ancora".split(), 0.0)
    full, tail = detect_full_restart_cut(words)  # default min_match_full=5, tol=1
    assert full == [] and tail == 0.0


def test_no_repeat_no_cut():
    words, _ = _mk("un discorso senza ripetizioni di alcun tipo".split(), 0.0)
    assert detect_all_retake_cuts(words) == []
    assert detect_full_restart_cut(words) == ([], 0.0)


def test_short_stumble_still_detected():
    # ripartenza breve classica: "quindi noi ... quindi noi andiamo"
    words, _ = _mk("quindi noi andiamo quindi noi andiamo a casa".split(), 0.0)
    cuts = detect_retake_cuts(words, min_match=3, window_s=10.0)
    assert len(cuts) == 1
    # tiene l'ultima ripresa
    assert _kept_text(words, cuts).endswith("quindi noi andiamo a casa")


def test_full_restart_result_is_contiguous_tail():
    # il taglio full non deve mai lasciare buchi interni alla ripresa tenuta
    take1, t1 = _mk("alfa beta gamma delta epsilon".split(), 0.0)
    take2, _ = _mk("alfa beta gamma delta epsilon zeta eta".split(), t1 + 1.2)
    words = take1 + take2
    cuts = detect_all_retake_cuts(words, min_match_full=4, restart_gap=0.6)
    kept = filter_words_outside_cuts(words, cuts)
    # le parole tenute sono consecutive nel tempo (nessun salto = nessun mix)
    for a, b in zip(kept, kept[1:]):
        assert b[0] >= a[0]
    assert _kept_text(words, cuts) == "alfa beta gamma delta epsilon zeta eta"
