"""QA performance/cache: memoizzazione di ffmpeg.probe e caching header dei media.

Copre, tutto OFFLINE (nessun ffprobe reale, subprocess sostituito):
- ffmpeg.probe memoizzato per (path, mtime, size): chiamate ripetute sullo
  STESSO file girano ffprobe UNA sola volta; se il file cambia (mtime/size) il
  probe viene rifatto (invalidazione della cache);
- endpoint di serving media (originale/thumbnail/export) espongono Cache-Control
  ed ETag deboli;
- If-None-Match corrispondente all'ETag -> 304 senza corpo.

Preambolo env identico agli altri moduli: get_settings() e' @lru_cache, il primo
modulo importato in una suite congela le Settings, quindi setdefault + percorsi
sempre da get_settings().
"""
import os
import tempfile
import uuid
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_qa_perf_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

import json  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

import app.services.ffmpeg as ffm  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Template, Video, VideoStatus  # noqa: E402
from app.security import get_login_rate_limiter  # noqa: E402

init_db()
get_settings().ensure_dirs()
client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker

# payload ffprobe minimo ma valido (durata + traccia video + audio)
_PROBE_PAYLOAD = {
    "format": {"duration": "8.0"},
    "streams": [
        {"codec_type": "video", "width": 1080, "height": 1920, "avg_frame_rate": "30/1"},
        {"codec_type": "audio"},
    ],
}


# --------------------------------------------------------------------------- #
# helper
# --------------------------------------------------------------------------- #
def _auth() -> dict:
    get_login_rate_limiter().clear()
    r = client.post("/api/auth/login", json={"password": get_settings().admin_password},
                    headers={"X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _real_file(suffix: str = ".mp4", data: bytes = b"finto-media") -> Path:
    p = Path(_TMP) / f"perf_{uuid.uuid4().hex}{suffix}"
    p.write_bytes(data)
    return p


def _make_video(**kw) -> str:
    base = dict(original_name="clip.mp4", stored_path="", duration=8.0,
                status=VideoStatus.READY)
    base.update(kw)
    with SessionLocal() as db:
        v = Video(**base)
        db.add(v)
        db.commit()
        return v.id


def _patch_probe_run(monkeypatch) -> list[int]:
    """Sostituisce ffmpeg._run con un finto ffprobe che conta le invocazioni.
    Ritorna la lista-contatore (len == numero di esecuzioni ffprobe reali)."""
    calls: list[int] = []

    def fake_run(cmd, timeout=120):
        calls.append(1)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_PROBE_PAYLOAD),
                                           stderr="")

    monkeypatch.setattr(ffm, "_run", fake_run)
    return calls


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    """La cache probe e' globale di processo: azzerala prima/dopo ogni test per
    conteggi deterministici anche in suite completa."""
    ffm._probe_cache.clear()
    yield
    ffm._probe_cache.clear()


@pytest.fixture(autouse=True)
def _db_cleanup():
    """Elimina SOLO le righe create dal test (DB condiviso in suite completa)."""
    with SessionLocal() as db:
        before = {vid for (vid,) in db.execute(select(Video.id))}
        before_tpl = {tid for (tid,) in db.execute(select(Template.id))}
    yield
    with SessionLocal() as db:
        for vid in [v for (v,) in db.execute(select(Video.id)) if v not in before]:
            db.delete(db.get(Video, vid))
        for tid in [t for (t,) in db.execute(select(Template.id)) if t not in before_tpl]:
            db.delete(db.get(Template, tid))
        db.commit()


# --------------------------------------------------------------------------- #
# 1. memoizzazione di probe
# --------------------------------------------------------------------------- #
def test_probe_memoized_single_ffprobe_on_repeated_calls(monkeypatch):
    calls = _patch_probe_run(monkeypatch)
    f = _real_file()
    first = ffm.probe(f)
    second = ffm.probe(f)
    third = ffm.probe(str(f))  # anche via stringa: stesso path -> cache hit
    assert len(calls) == 1, "ffprobe dovrebbe girare una sola volta (memoizzato)"
    assert first == second == third
    assert first["duration"] == 8.0 and first["width"] == 1080


def test_probe_reprobed_when_mtime_changes(monkeypatch):
    calls = _patch_probe_run(monkeypatch)
    f = _real_file()
    ffm.probe(f)
    assert len(calls) == 1
    # cambia SOLO l'mtime (dimensione invariata): la chiave di cache cambia
    st = f.stat()
    new_ns = st.st_mtime_ns + 5_000_000_000  # +5s, granularita' sicura
    os.utime(f, ns=(new_ns, new_ns))
    ffm.probe(f)
    assert len(calls) == 2, "mtime cambiato -> la cache deve invalidarsi e ri-probare"


def test_probe_reprobed_when_size_changes(monkeypatch):
    calls = _patch_probe_run(monkeypatch)
    f = _real_file(data=b"corto")
    ffm.probe(f)
    f.write_bytes(b"contenuto piu' lungo di prima")  # size diversa -> chiave diversa
    ffm.probe(f)
    assert len(calls) == 2


def test_probe_not_cached_for_missing_file(monkeypatch):
    # file inesistente: os.stat fallisce -> nessuna memoizzazione, ffprobe ogni volta
    calls = _patch_probe_run(monkeypatch)
    missing = str(Path(_TMP) / f"non_esiste_{uuid.uuid4().hex}.mp4")
    ffm.probe(missing)
    ffm.probe(missing)
    assert len(calls) == 2


def test_probe_cache_respects_lru_limit(monkeypatch):
    # oltre probe_cache_size: la cache non cresce all'infinito (evizione LRU)
    calls = _patch_probe_run(monkeypatch)
    monkeypatch.setattr(get_settings(), "probe_cache_size", 3, raising=False)
    files = [_real_file() for _ in range(5)]
    for f in files:
        ffm.probe(f)
    assert len(calls) == 5
    assert len(ffm._probe_cache) <= 3
    # il piu' vecchio e' stato evitto: riprobarlo rifa' ffprobe
    ffm.probe(files[0])
    assert len(calls) == 6


# --------------------------------------------------------------------------- #
# 2. caching header sui media serviti
# --------------------------------------------------------------------------- #
def _cache_headers_present(resp, *, immutable: bool):
    cc = resp.headers.get("Cache-Control", "")
    assert "private" in cc
    assert f"max-age={get_settings().media_cache_max_age}" in cc
    assert ("immutable" in cc) is immutable
    etag = resp.headers.get("ETag", "")
    assert etag.startswith('W/"') and etag.endswith('"')


def test_original_file_has_cache_headers():
    f = _real_file(suffix=".mp4")
    vid = _make_video(stored_path=str(f))
    r = client.get(f"/api/videos/{vid}/file", headers=_auth())
    assert r.status_code == 200
    _cache_headers_present(r, immutable=False)


def test_thumbnail_has_cache_headers():
    f = _real_file(suffix=".jpg", data=b"jpgdata")
    vid = _make_video(thumbnail_path=str(f))
    r = client.get(f"/api/videos/{vid}/thumbnail", headers=_auth())
    assert r.status_code == 200
    _cache_headers_present(r, immutable=False)
    assert r.headers.get("Content-Type", "").startswith("image/jpeg")


def test_export_file_has_immutable_cache_headers():
    f = _real_file(suffix=".mp4")
    vid = _make_video(exported_path=str(f))
    r = client.get(f"/api/videos/{vid}/export/file", headers=_auth())
    assert r.status_code == 200
    _cache_headers_present(r, immutable=True)


def test_export_download_has_immutable_cache_headers():
    f = _real_file(suffix=".mp4")
    vid = _make_video(original_name="il mio video.mp4", exported_path=str(f))
    r = client.get(f"/api/videos/{vid}/export/download", headers=_auth())
    assert r.status_code == 200
    _cache_headers_present(r, immutable=True)
    assert "attachment" in r.headers.get("Content-Disposition", "")


# --------------------------------------------------------------------------- #
# 3. rivalidazione condizionale -> 304
# --------------------------------------------------------------------------- #
def test_if_none_match_returns_304_thumbnail():
    f = _real_file(suffix=".jpg", data=b"jpgdata")
    vid = _make_video(thumbnail_path=str(f))
    auth = _auth()
    first = client.get(f"/api/videos/{vid}/thumbnail", headers=auth)
    etag = first.headers["ETag"]
    r = client.get(f"/api/videos/{vid}/thumbnail",
                   headers={**auth, "If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""  # nessun corpo ri-trasferito
    assert r.headers.get("ETag") == etag  # stesso validatore in risposta


def test_if_none_match_returns_304_export():
    f = _real_file(suffix=".mp4")
    vid = _make_video(exported_path=str(f))
    auth = _auth()
    first = client.get(f"/api/videos/{vid}/export/file", headers=auth)
    etag = first.headers["ETag"]
    r = client.get(f"/api/videos/{vid}/export/file",
                   headers={**auth, "If-None-Match": etag})
    assert r.status_code == 304
    assert "immutable" in r.headers.get("Cache-Control", "")


def test_stale_if_none_match_returns_200():
    f = _real_file(suffix=".mp4")
    vid = _make_video(exported_path=str(f))
    auth = _auth()
    r = client.get(f"/api/videos/{vid}/export/file",
                   headers={**auth, "If-None-Match": 'W/"deadbeef-0"'})
    assert r.status_code == 200  # ETag non combacia: file servito per intero
