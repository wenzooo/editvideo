"""QA API test: PUT/GET /api/videos/{id}/subtitles (routers/subtitles.py).

Logica chiave MAI testata prima: la PUT preserva i word-timestamp (karaoke)
SOLO per i segmenti rientrati con (round(start,2), round(end,2), text)
invariati; ogni segmento realmente modificato (testo o tempi oltre il round a
2 decimali) perde le words. Tutto OFFLINE via TestClient, nessun ffmpeg.

Ambiente isolato configurato PRIMA di importare l'app (stesso preambolo dei
moduli esistenti: setdefault non sovrascrive nulla di gia' impostato, quindi
in suite completa vincono le env del primo modulo importato — per questo a
runtime si usano SEMPRE i percorsi di get_settings(), mai _TMP).

Tutti i test sono VERDI: i punti sottili (collisione delle chiavi dopo il
round a 2 decimali, filtro silenzioso dei segmenti degeneri) sono documentati
come CHARACTERIZATION del comportamento reale, non asserzioni di correttezza.
"""
import os
import tempfile
import uuid
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_qa_subtitles_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import SubtitleSegment, Video, VideoStatus  # noqa: E402
from app.security import get_login_rate_limiter  # noqa: E402

init_db()
get_settings().ensure_dirs()
client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker

# words karaoke di riferimento: [[start, end, "parola"], ...]
W1 = [[0.0, 0.4, "ciao"], [0.4, 0.9, "a"], [0.9, 1.5, "tutti"]]
W2 = [[2.0, 2.5, "secondo"], [2.5, 3.0, "segmento"]]


# --------------------------------------------------------------------------- #
# helper
# --------------------------------------------------------------------------- #
def _auth() -> dict:
    # solo login RIUSCITI (password giusta): il clear evita che eventuali 429
    # lasciati da altri moduli blocchino questo login, e non lascia residui.
    get_login_rate_limiter().clear()
    r = client.post("/api/auth/login", json={"password": get_settings().admin_password},
                    headers={"X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _make_video(segments: list[dict] | None = None, **kw) -> str:
    """Crea un video (default REVIEW) con eventuali SubtitleSegment gia' a DB."""
    base = dict(original_name="subs.mp4", stored_path="", duration=10.0,
                status=VideoStatus.REVIEW)
    base.update(kw)
    with SessionLocal() as db:
        v = Video(**base)
        db.add(v)
        db.flush()
        for i, seg in enumerate(segments or []):
            db.add(SubtitleSegment(video_id=v.id, idx=i, **seg))
        db.commit()
        return v.id


def _seed_two_segments() -> str:
    """Video REVIEW con 2 caption entrambe con words popolate (karaoke)."""
    return _make_video(segments=[
        dict(start=0.0, end=1.5, text="ciao a tutti", words=W1),
        dict(start=2.0, end=3.0, text="secondo segmento", words=W2),
    ])


def _put(video_id: str, segments: list[dict], headers: dict):
    return client.put(f"/api/videos/{video_id}/subtitles",
                      json={"segments": segments}, headers=headers)


def _get(video_id: str, headers: dict):
    return client.get(f"/api/videos/{video_id}/subtitles", headers=headers)


IDENTICAL_PAYLOAD = [
    {"start": 0.0, "end": 1.5, "text": "ciao a tutti"},
    {"start": 2.0, "end": 3.0, "text": "secondo segmento"},
]


@pytest.fixture(autouse=True)
def _db_cleanup():
    """Snapshot dei video esistenti; a fine test si eliminano SOLO le righe
    nuove (i segmenti seguono il video via cascade ORM). In suite completa il
    DB e' condiviso tra moduli: mai toccare righe altrui."""
    with SessionLocal() as db:
        before_videos = {vid for (vid,) in db.execute(select(Video.id))}
    yield
    with SessionLocal() as db:
        new_video_ids = [vid for (vid,) in db.execute(select(Video.id))
                         if vid not in before_videos]
        for vid in new_video_ids:
            db.delete(db.get(Video, vid))  # cascade ORM: elimina anche i segmenti
        db.commit()


# --------------------------------------------------------------------------- #
# 1. PUT identica: le words (karaoke) sopravvivono
# --------------------------------------------------------------------------- #
def test_put_identical_segments_preserves_words():
    vid = _seed_two_segments()
    headers = _auth()
    r = _put(vid, IDENTICAL_PAYLOAD, headers)
    assert r.status_code == 200
    body = r.json()
    assert [s["text"] for s in body] == ["ciao a tutti", "secondo segmento"]
    assert body[0]["words"] == W1
    assert body[1]["words"] == W2
    # anche in rilettura (GET) le words restano persistite a DB
    g = _get(vid, headers)
    assert g.status_code == 200
    got = g.json()
    assert [s["words"] for s in got] == [W1, W2]
    assert [s["idx"] for s in got] == [0, 1]


# --------------------------------------------------------------------------- #
# 2. testo modificato: SOLO quel segmento perde il karaoke
# --------------------------------------------------------------------------- #
def test_put_edited_text_drops_words_only_for_that_segment():
    vid = _seed_two_segments()
    headers = _auth()
    r = _put(vid, [
        {"start": 0.0, "end": 1.5, "text": "ciao a tutti quanti"},  # testo cambiato
        {"start": 2.0, "end": 3.0, "text": "secondo segmento"},     # invariato
    ], headers)
    assert r.status_code == 200
    body = r.json()
    assert body[0]["text"] == "ciao a tutti quanti"
    assert not body[0]["words"]        # None (o []): karaoke perso
    assert body[1]["words"] == W2      # l'altro lo conserva


# --------------------------------------------------------------------------- #
# 3. tempi shiftati oltre il round a 2 decimali: words perse
# --------------------------------------------------------------------------- #
def test_put_shifted_times_beyond_round2_drops_words():
    vid = _seed_two_segments()
    headers = _auth()
    r = _put(vid, [
        # start 0.0 -> 0.02: round(0.02,2)=0.02 != round(0.0,2)=0.0 -> chiave diversa
        {"start": 0.02, "end": 1.5, "text": "ciao a tutti"},
        {"start": 2.0, "end": 3.0, "text": "secondo segmento"},
    ], headers)
    assert r.status_code == 200
    body = r.json()
    assert not body[0]["words"]        # shift reale: karaoke perso
    assert body[1]["words"] == W2


def test_put_shift_within_round2_keeps_words():
    # controprova: uno shift SOTTO la tolleranza del round a 2 decimali
    # (0.0 -> 0.001, round -> 0.0) mantiene la stessa chiave e conserva le words.
    vid = _seed_two_segments()
    headers = _auth()
    r = _put(vid, [
        {"start": 0.001, "end": 1.5, "text": "ciao a tutti"},
        {"start": 2.0, "end": 3.0, "text": "secondo segmento"},
    ], headers)
    assert r.status_code == 200
    assert r.json()[0]["words"] == W1


# --------------------------------------------------------------------------- #
# 4. CHARACTERIZATION: collisione della chiave dopo round(...,2)
# --------------------------------------------------------------------------- #
def test_put_round2_key_collision_last_segment_wins_characterization():
    # QA CHARACTERIZATION (subtitles.py:38-41): la mappa `existing` e' indicizzata
    # per (round(start,2), round(end,2)). Due segmenti REALI distinti a DB —
    # start 1.001 e 1.004, end 2.0 e 2.004, stesso testo — collidono sulla stessa
    # chiave (1.0, 2.0): nella dict-comprehension l'ULTIMO (idx piu' alto) vince
    # e sovrascrive il primo. Alla PUT identica, ENTRAMBI i segmenti pescano la
    # stessa entry: il primo PERDE le proprie words WA ed EREDITA le WB del
    # secondo. Comportamento reale documentato, non necessariamente corretto.
    WA = [[1.0, 1.5, "prima"], [1.5, 2.0, "voce"]]
    WB = [[1.0, 1.5, "seconda"], [1.5, 2.0, "voce"]]
    vid = _make_video(segments=[
        dict(start=1.001, end=2.0, text="doppione", words=WA),
        dict(start=1.004, end=2.004, text="doppione", words=WB),
    ])
    headers = _auth()
    r = _put(vid, [
        {"start": 1.001, "end": 2.0, "text": "doppione"},
        {"start": 1.004, "end": 2.004, "text": "doppione"},
    ], headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    # il primo segmento NON riottiene le sue WA: eredita le WB del collidente
    assert body[0]["words"] == WB
    assert body[1]["words"] == WB
    assert all(s["words"] != WA for s in body)  # WA sono andate perse


# --------------------------------------------------------------------------- #
# 5. guardie e input degeneri
# --------------------------------------------------------------------------- #
def test_put_on_transcribing_video_409():
    vid = _make_video(status=VideoStatus.TRANSCRIBING,
                      segments=[dict(start=0.0, end=1.0, text="occupato", words=W1)])
    headers = _auth()
    r = _put(vid, [{"start": 0.0, "end": 1.0, "text": "occupato"}], headers)
    assert r.status_code == 409
    # i segmenti esistenti restano intatti
    g = _get(vid, headers)
    assert g.status_code == 200
    assert [s["text"] for s in g.json()] == ["occupato"]


def test_put_segment_end_zero_rejected_422_by_schema():
    # end=0 viola il vincolo di schema Field(gt=0) su SubtitleSegmentIn
    # (schemas.py:130): l'INTERA PUT e' respinta con 422 prima del router.
    vid = _seed_two_segments()
    headers = _auth()
    r = _put(vid, [{"start": 0.0, "end": 0.0, "text": "degenere"}], headers)
    assert r.status_code == 422
    # nulla e' stato toccato: i segmenti originali sono ancora li'
    assert [s["words"] for s in _get(vid, headers).json()] == [W1, W2]


def test_put_segment_end_before_start_silently_dropped_characterization():
    # QA CHARACTERIZATION: lo schema NON impone end > start (scelta documentata
    # in schemas.py:124-131); un segmento con end<=start ma end>0 passa la
    # validazione e viene FILTRATO in silenzio dal router (subtitles.py:43-46):
    # risposta 200 senza il segmento degenere, nessun 422.
    vid = _make_video()
    headers = _auth()
    r = _put(vid, [
        {"start": 5.0, "end": 3.0, "text": "inverso"},   # end < start: scartato
        {"start": 6.0, "end": 7.0, "text": "valido"},
    ], headers)
    assert r.status_code == 200
    body = r.json()
    assert [s["text"] for s in body] == ["valido"]  # il degenere sparisce senza errore


def test_put_blank_text_segment_silently_dropped_characterization():
    # QA CHARACTERIZATION: testo vuoto/solo spazi non produce errore: il router
    # filtra `s.text.strip()` (subtitles.py:44) e il segmento sparisce dalla
    # risposta e dal DB. Una PUT di soli segmenti vuoti svuota i sottotitoli.
    vid = _seed_two_segments()
    headers = _auth()
    r = _put(vid, [
        {"start": 0.0, "end": 1.5, "text": "   "},               # scartato
        {"start": 2.0, "end": 3.0, "text": "secondo segmento"},  # tenuto
    ], headers)
    assert r.status_code == 200
    body = r.json()
    assert [s["text"] for s in body] == ["secondo segmento"]
    assert body[0]["words"] == W2      # invariato: conserva il karaoke
    assert body[0]["idx"] == 0         # re-indicizzato da zero dopo il filtro


# --------------------------------------------------------------------------- #
# 6. GET: lista vuota e 404
# --------------------------------------------------------------------------- #
def test_get_subtitles_video_without_segments_empty_list():
    vid = _make_video()
    r = _get(vid, _auth())
    assert r.status_code == 200
    assert r.json() == []


def test_get_subtitles_unknown_video_404():
    r = _get(uuid.uuid4().hex, _auth())
    assert r.status_code == 404
