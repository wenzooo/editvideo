"""Test dell'endpoint on-demand POST /api/videos/{id}/retakes.

Rileva i doppioni (ripartenze brevi + riprese dell'intero discorso) dai
SubtitleSegment gia' trascritti e li FONDE con i tagli esistenti, senza
azzerare i tagli manuali/silenzi.

Ambiente isolato configurato PRIMA di importare l'app (come test_jobs_cancel /
test_security): DB sqlite temporaneo + segreti espliciti via setdefault, cosi'
l'ordine di import tra i moduli di test non rompe nulla.
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_retakes_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402  -> verifica implicita che `import app.main` sia ok
from app.models import SubtitleSegment, Video, VideoStatus  # noqa: E402
from app.security import get_login_rate_limiter  # noqa: E402

init_db()
client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker


def _token() -> str:
    get_login_rate_limiter().clear()
    r = client.post("/api/auth/login", json={"password": get_settings().admin_password},
                    headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 200
    return r.json()["token"]


def _auth() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


# stesso incipit ("ciao a tutti oggi parliamo") ripetuto a distanza: la seconda
# occorrenza e' l'ultima ripresa buona. Diviso su DUE segmenti per esercitare il
# flatten ordinato per idx.
_TAKE1 = [[0.0, 0.3, "ciao"], [0.4, 0.7, "a"], [0.8, 1.1, "tutti"],
          [1.2, 1.5, "oggi"], [1.6, 1.9, "parliamo"]]
_TAKE2 = [[60.0, 60.3, "ciao"], [60.4, 60.7, "a"], [60.8, 61.1, "tutti"],
          [61.2, 61.5, "oggi"], [61.6, 61.9, "parliamo"]]


def _make_video(seg_words, *, duration=90.0, status=VideoStatus.REVIEW, cuts=None) -> str:
    """Crea un video + i suoi SubtitleSegment. `seg_words` e' una lista di
    segmenti, ognuno lista di [start, end, text] per-parola (o [] = words None)."""
    with SessionLocal() as db:
        v = Video(original_name="clip.mp4", stored_path="/tmp/clip.mp4",
                  status=status, duration=duration, cuts=cuts or [])
        db.add(v)
        db.flush()
        for i, ws in enumerate(seg_words):
            db.add(SubtitleSegment(
                video_id=v.id, idx=i,
                start=ws[0][0] if ws else 0.0,
                end=ws[-1][1] if ws else 0.0,
                text=" ".join(str(w[2]) for w in ws),
                words=[list(w) for w in ws] if ws else None,
            ))
        db.commit()
        return v.id


# --------------------------------------------------------------------------- #
# happy path: full-take rilevato e fuso con i tagli manuali esistenti
# --------------------------------------------------------------------------- #
def test_retakes_detects_full_take_and_merges_manual_cut():
    # taglio manuale esistente a [70,75] (dentro l'ultima ripresa, va preservato)
    vid = _make_video([_TAKE1, _TAKE2], cuts=[{"start": 70.0, "end": 75.0}])
    r = client.post(f"/api/videos/{vid}/retakes", headers=_auth())
    assert r.status_code == 200, r.text
    cuts = r.json()["cuts"]
    # 2 tagli, ordinati: la ripresa abortita (0->~60) + il taglio manuale preservato
    assert len(cuts) == 2
    assert cuts[0]["start"] == 0.0
    assert cuts[0]["end"] == pytest.approx(59.85, abs=0.05)
    assert cuts[1]["start"] == 70.0 and cuts[1]["end"] == 75.0
    # persistito a DB
    with SessionLocal() as db:
        assert len(db.get(Video, vid).cuts) == 2


def test_retakes_no_repetition_leaves_cuts_untouched():
    words = [[[0.0, 0.3, "uno"], [0.5, 0.8, "due"], [1.0, 1.3, "tre"], [1.5, 1.8, "quattro"]]]
    vid = _make_video(words, cuts=[{"start": 2.0, "end": 3.0}])
    r = client.post(f"/api/videos/{vid}/retakes", headers=_auth())
    assert r.status_code == 200, r.text
    # nessun doppione: resta solo il taglio manuale gia' presente
    assert r.json()["cuts"] == [{"start": 2.0, "end": 3.0}]


# --------------------------------------------------------------------------- #
# errori: sottotitoli mancanti (400), video in lavorazione (409), 404, auth
# --------------------------------------------------------------------------- #
def test_retakes_without_any_segment_returns_400():
    vid = _make_video([])  # nessun sottotitolo
    r = client.post(f"/api/videos/{vid}/retakes", headers=_auth())
    assert r.status_code == 400
    assert "sottotitoli" in r.json()["detail"].lower()


def test_retakes_with_segments_but_no_words_returns_400():
    vid = _make_video([[]])  # un segmento, ma words = None
    r = client.post(f"/api/videos/{vid}/retakes", headers=_auth())
    assert r.status_code == 400


def test_retakes_busy_video_returns_409():
    vid = _make_video([_TAKE1, _TAKE2], status=VideoStatus.TRANSCRIBING)
    r = client.post(f"/api/videos/{vid}/retakes", headers=_auth())
    assert r.status_code == 409


def test_retakes_unknown_video_returns_404():
    r = client.post("/api/videos/nonesistente/retakes", headers=_auth())
    assert r.status_code == 404


def test_retakes_requires_auth():
    vid = _make_video([_TAKE1, _TAKE2])
    client.cookies.clear()  # scarta il cookie di sessione lasciato dai login precedenti
    assert client.post(f"/api/videos/{vid}/retakes").status_code == 401
