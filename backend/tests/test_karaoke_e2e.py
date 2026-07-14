"""End-to-end (offline) del karaoke: dai word-timestamp della trascrizione,
attraverso il rimappaggio sui tagli, fino all'.ass colorato per l'export.

Verifica il PUNTO che l'utente vedeva rotto ("non si colora"): il colore
per-parola deve arrivare fino all'ASS, col colore scelto e nell'ordine BGR.
Solo moduli puri: nessun whisper/db/ffmpeg.
"""
from app.services.styles import build_ass, hex_to_ass_colour
from app.services.timeline import keep_intervals, remap_segments_detailed


def _seg(start, end, text, words):
    return {"start": start, "end": end, "text": text, "words": words}


def test_hex_to_ass_is_bgr_opaque():
    # rosso #FF0000 -> BGR 0000FF, alpha 00 (opaco)
    assert hex_to_ass_colour("#FF0000") == "&H000000FF&"
    # verde #00FF00 -> 00FF00
    assert hex_to_ass_colour("#00FF00") == "&H0000FF00&"
    # input assente/malformato -> giallo di default
    assert hex_to_ass_colour(None) == "&H0000FFFF&"
    assert hex_to_ass_colour("nope") == "&H0000FFFF&"


def test_karaoke_ass_one_dialogue_per_word_colored():
    seg = _seg(0.0, 2.0, "ciao a tutti",
               [[0.0, 0.5, "ciao"], [0.6, 1.0, "a"], [1.1, 2.0, "tutti"]])
    ass = build_ass([seg], "karaoke_word", karaoke_color="#FF0000")
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    # una riga per parola
    assert len(dialogues) == 3
    # ogni riga contiene TUTTE le parole (caption intera visibile) e colora
    # una sola parola col rosso richiesto (&H000000FF&)
    for ln in dialogues:
        for w in ("ciao", "a", "tutti"):
            assert w in ln
        assert ln.count("&H000000FF&") == 1
    # la parola attiva avanza riga per riga: "ciao", poi "a", poi "tutti"
    active = [ln.split("&H000000FF&}")[1].split("{")[0] for ln in dialogues]
    assert active == ["ciao", "a", "tutti"]


def test_karaoke_words_survive_a_cut():
    # taglio che rimuove la parola centrale "a" [0.55,1.05]: restano ciao+tutti,
    # con i tempi rimappati sulla timeline di output (contigui).
    seg = _seg(0.0, 2.0, "ciao a tutti",
               [[0.0, 0.5, "ciao"], [0.6, 1.0, "a"], [1.1, 2.0, "tutti"]])
    keeps = keep_intervals(2.0, cuts=[{"start": 0.55, "end": 1.05}])
    remapped = remap_segments_detailed([seg], keeps)
    assert len(remapped) == 1
    kept_words = [w[2] for w in remapped[0]["words"]]
    assert kept_words == ["ciao", "tutti"]
    ass = build_ass(remapped, "karaoke_word", karaoke_color="#00FF00")
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogues) == 2  # una per parola sopravvissuta
    assert all(ln.count("&H0000FF00&") == 1 for ln in dialogues)


def test_karaoke_without_words_falls_back_static():
    # caption editata a mano (words=None): niente karaoke, un solo Dialogue statico
    seg = _seg(0.0, 2.0, "testo editato", None)
    ass = build_ass([seg], "karaoke_word", karaoke_color="#FF0000")
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogues) == 1
    assert "&H000000FF&" not in dialogues[0]  # nessuna evidenziazione
