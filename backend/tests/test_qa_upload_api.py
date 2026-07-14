"""QA API test: upload / listing / delete / autocut / patch trim / jobs / meta.

Copre backend/app/routers/videos.py e jobs.py via TestClient, tutto OFFLINE:
le funzioni ffmpeg usate dall'upload (ff.probe, ff.make_thumbnail) e il
rilevamento silenzi (auto_cuts_for) sono monkeypatchati, mai eseguiti davvero.

Ambiente isolato configurato PRIMA di importare l'app (stesso preambolo dei
moduli esistenti: setdefault non sovrascrive nulla di gia' impostato, quindi
in suite completa vincono le env del primo modulo importato — per questo a
runtime si usano SEMPRE i percorsi di get_settings(), mai _TMP).

Include 2 TEST ROSSI che documentano bug confermati (QA-08, QA-19): asseriscono
il comportamento CORRETTO atteso e oggi falliscono. Non vanno "aggiustati".
"""
import os
import tempfile
import uuid
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_qa_upload_api_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")  # nessun worker embedded nei test

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

import app.routers.videos as videos_router  # noqa: E402
import app.services.silence as silence_mod  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Job, JobStatus, JobType, Template, Video, VideoStatus  # noqa: E402
from app.security import get_login_rate_limiter  # noqa: E402
from app.services import ffmpeg as ff_mod  # noqa: E402

init_db()
get_settings().ensure_dirs()
client = TestClient(app)  # niente `with`: nessun lifespan -> nessun worker

FAKE_META = {"duration": 12.0, "width": 1920, "height": 1080, "fps": 30.0, "has_audio": True}


# --------------------------------------------------------------------------- #
# helper
# --------------------------------------------------------------------------- #
def _auth() -> dict:
    # solo login RIUSCITI (password giusta): il clear evita che eventuali 429
    # lasciati da altri moduli blocchino questo login, e non lascia residui.
    get_login_rate_limiter().clear()
    r = client.post("/api/auth/login", json={"password": get_settings().admin_password},
                    headers={"X-Forwarded-For": "8.8.4.4"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _make_video(**kw) -> str:
    base = dict(original_name="clip.mp4", stored_path="", duration=10.0,
                status=VideoStatus.UPLOADED)
    base.update(kw)
    with SessionLocal() as db:
        v = Video(**base)
        db.add(v)
        db.commit()
        return v.id


def _video_row(video_id: str) -> Video | None:
    with SessionLocal() as db:
        return db.get(Video, video_id)


@pytest.fixture(autouse=True)
def _db_cleanup():
    """Snapshot di video/template esistenti; a fine test si eliminano SOLO le
    righe nuove (i job seguono il video via cascade ORM) e i loro file su disco.
    In suite completa il DB e' condiviso tra moduli: mai toccare righe altrui."""
    with SessionLocal() as db:
        before_videos = {vid for (vid,) in db.execute(select(Video.id))}
        before_templates = {tid for (tid,) in db.execute(select(Template.id))}
    yield
    with SessionLocal() as db:
        new_video_ids = [vid for (vid,) in db.execute(select(Video.id))
                         if vid not in before_videos]
        for vid in new_video_ids:
            v = db.get(Video, vid)
            for p in (v.stored_path, v.thumbnail_path, v.exported_path):
                if p:
                    Path(p).unlink(missing_ok=True)
            db.delete(v)  # cascade ORM: elimina anche job e segmenti collegati
        new_template_ids = [tid for (tid,) in db.execute(select(Template.id))
                            if tid not in before_templates]
        for tid in new_template_ids:
            db.delete(db.get(Template, tid))
        db.commit()


@pytest.fixture
def ffmpeg_ok(monkeypatch):
    """Upload senza ffmpeg reale: probe/make_thumbnail finti nel modulo che il
    router usa (videos.py fa `from ..services import ffmpeg as ff`, quindi
    videos_router.ff E' app.services.ffmpeg: si patcha li')."""
    def fake_probe(path):
        return dict(FAKE_META)

    def fake_thumb(src, dst, at=1.0):
        Path(dst).write_bytes(b"jpg")

    monkeypatch.setattr(videos_router.ff, "probe", fake_probe)
    monkeypatch.setattr(videos_router.ff, "make_thumbnail", fake_thumb)


def _upload(files, headers, template_id: str | None = None):
    data = {"template_id": template_id} if template_id else {}
    return client.post("/api/videos/upload", files=files, data=data, headers=headers)


# --------------------------------------------------------------------------- #
# 1. upload
# --------------------------------------------------------------------------- #
def test_upload_ok_creates_video_uploaded(ffmpeg_ok):
    r = _upload([("files", ("clip qa.mp4", b"finto-mp4", "video/mp4"))], _auth())
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert len(body["created"]) == 1
    out = body["created"][0]
    assert out["status"] == "uploaded"
    assert out["original_name"] == "clip qa.mp4"
    assert out["duration"] == FAKE_META["duration"]
    assert out["width"] == 1920 and out["height"] == 1080
    row = _video_row(out["id"])
    assert row is not None and row.status == VideoStatus.UPLOADED
    assert Path(row.stored_path).exists()


def test_upload_unicode_filename_preserved(ffmpeg_ok):
    name = "vidèo prova ✨.mp4"
    r = _upload([("files", (name, b"finto-mp4", "video/mp4"))], _auth())
    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert body["created"][0]["original_name"] == name


def test_upload_txt_extension_rejected_no_video(ffmpeg_ok):
    with SessionLocal() as db:
        n_before = len(db.execute(select(Video.id)).all())
    r = _upload([("files", ("note.txt", b"ciao", "text/plain"))], _auth())
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == []
    assert len(body["errors"]) == 1
    assert body["errors"][0]["name"] == "note.txt"
    assert "non supportato" in body["errors"][0]["reason"]
    with SessionLocal() as db:
        assert len(db.execute(select(Video.id)).all()) == n_before


def test_upload_probe_failure_no_residual_file(monkeypatch):
    def boom(path):
        raise ff_mod.FFmpegError("ffprobe fallito: file corrotto")

    monkeypatch.setattr(videos_router.ff, "probe", boom)
    originals = get_settings().originals_dir
    before = set(originals.glob("*"))
    r = _upload([("files", ("rotto.mp4", b"garbage", "video/mp4"))], _auth())
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == []
    assert len(body["errors"]) == 1
    assert "corrotto" in body["errors"][0]["reason"]
    # nessun file residuo: il dst scritto prima del probe deve essere rimosso
    assert set(originals.glob("*")) == before


def test_upload_unknown_template_404_no_video(ffmpeg_ok):
    with SessionLocal() as db:
        n_before = len(db.execute(select(Video.id)).all())
    r = _upload([("files", ("clip.mp4", b"finto", "video/mp4"))], _auth(),
                template_id="tpl-inesistente")
    assert r.status_code == 404
    with SessionLocal() as db:
        assert len(db.execute(select(Video.id)).all()) == n_before


def test_upload_mixed_batch_created_and_errors(ffmpeg_ok):
    r = _upload([("files", ("buono.mp4", b"finto", "video/mp4")),
                 ("files", ("cattivo.txt", b"nope", "text/plain"))], _auth())
    assert r.status_code == 200
    body = r.json()
    assert len(body["created"]) == 1 and body["created"][0]["original_name"] == "buono.mp4"
    assert len(body["errors"]) == 1 and body["errors"][0]["name"] == "cattivo.txt"


def test_upload_with_auto_transcribe_template_enqueues_job(ffmpeg_ok):
    tpl_name = f"QA upload {uuid.uuid4().hex[:8]}"  # name unique a DB
    with SessionLocal() as db:
        tpl = Template(name=tpl_name, auto_transcribe=True)
        db.add(tpl)
        db.commit()
        tpl_id = tpl.id
    r = _upload([("files", ("auto.mp4", b"finto", "video/mp4"))], _auth(),
                template_id=tpl_id)
    assert r.status_code == 200
    vid = r.json()["created"][0]["id"]
    with SessionLocal() as db:
        jobs = db.execute(select(Job).where(Job.video_id == vid)).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].type == JobType.TRANSCRIBE
        assert jobs[0].status == JobStatus.QUEUED


# --------------------------------------------------------------------------- #
# 2. listing
# --------------------------------------------------------------------------- #
def test_list_videos_filters_by_status():
    up_id = _make_video(original_name="up.mp4", status=VideoStatus.UPLOADED)
    ready_id = _make_video(original_name="rd.mp4", status=VideoStatus.READY)
    r = client.get("/api/videos", params={"status": "uploaded"}, headers=_auth())
    assert r.status_code == 200
    body = r.json()
    ids = {v["id"] for v in body}
    assert up_id in ids and ready_id not in ids
    assert all(v["status"] == "uploaded" for v in body)


def test_list_videos_unknown_status_rejected():
    # §9 (decisione presa): uno status inesistente NON ritorna in silenzio una
    # lista vuota (indistinguibile da "nessun video in quello stato") ma 422 con
    # l'elenco degli stati ammessi.
    _make_video(original_name="qualcosa.mp4", status=VideoStatus.UPLOADED)
    r = client.get("/api/videos", params={"status": "stato-che-non-esiste"},
                   headers=_auth())
    assert r.status_code == 422
    assert "uploaded" in r.json()["detail"]


def test_get_video_random_id_404():
    r = client.get(f"/api/videos/{uuid.uuid4().hex}", headers=_auth())
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# 3. delete
# --------------------------------------------------------------------------- #
def test_delete_video_with_active_job_409():
    vid = _make_video(original_name="occupato.mp4")
    with SessionLocal() as db:
        db.add(Job(video_id=vid, type=JobType.EXPORT, status=JobStatus.QUEUED))
        db.commit()
    r = client.delete(f"/api/videos/{vid}", headers=_auth())
    assert r.status_code == 409
    assert _video_row(vid) is not None  # non eliminato


def test_delete_free_video_removes_row_and_file():
    stored = get_settings().originals_dir / f"qa_del_{uuid.uuid4().hex}.mp4"
    stored.write_bytes(b"finto-mp4")
    vid = _make_video(original_name="libero.mp4", stored_path=str(stored))
    r = client.delete(f"/api/videos/{vid}", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert _video_row(vid) is None
    assert not stored.exists()  # file originale rimosso da disco


# --------------------------------------------------------------------------- #
# 4. autocut
# --------------------------------------------------------------------------- #
def test_autocut_saves_and_merges_cuts(monkeypatch):
    # il router fa `from ..services.silence import auto_cuts_for` DENTRO la
    # funzione: si patcha nel modulo sorgente, risolto a ogni chiamata.
    monkeypatch.setattr(silence_mod, "auto_cuts_for",
                        lambda *a, **kw: [{"start": 2.0, "end": 3.0}])
    vid = _make_video(original_name="silenzi.mp4", duration=10.0,
                      cuts=[{"start": 2.5, "end": 4.0}])
    r = client.post(f"/api/videos/{vid}/autocut", headers=_auth())
    assert r.status_code == 200
    # [2.0,3.0] rilevato + [2.5,4.0] esistente -> fusi in [2.0,4.0]
    assert [(c["start"], c["end"]) for c in r.json()["cuts"]] == [(2.0, 4.0)]
    row = _video_row(vid)
    assert row.cuts == [{"start": 2.0, "end": 4.0}]  # persistito a DB


def test_autocut_on_busy_video_409(monkeypatch):
    called = []
    monkeypatch.setattr(silence_mod, "auto_cuts_for",
                        lambda *a, **kw: called.append(1) or [])
    vid = _make_video(original_name="busy.mp4", status=VideoStatus.TRANSCRIBING)
    r = client.post(f"/api/videos/{vid}/autocut", headers=_auth())
    assert r.status_code == 409
    assert called == []  # respinto PRIMA di toccare il rilevamento


def test_autocut_detection_failure_500_and_cuts_untouched(monkeypatch):
    # QA-07: detect_silences ora SOLLEVA su ffmpeg fallito (mai [] silenzioso);
    # l'endpoint deve rispondere 500 con messaggio esplicito, senza toccare i
    # tagli gia' salvati.
    def boom(*a, **kw):
        raise RuntimeError("ffmpeg silencedetect fallito (returncode=1)")
    monkeypatch.setattr(silence_mod, "auto_cuts_for", boom)
    vid = _make_video(original_name="rotto.mp4", duration=10.0,
                      cuts=[{"start": 1.0, "end": 2.0}])
    r = client.post(f"/api/videos/{vid}/autocut", headers=_auth())
    assert r.status_code == 500
    assert "Rilevamento silenzi fallito" in r.json()["detail"]
    row = _video_row(vid)
    assert row.cuts == [{"start": 1.0, "end": 2.0}]  # tagli invariati


# --------------------------------------------------------------------------- #
# 5. TEST ROSSO — PATCH trim incoerente
# --------------------------------------------------------------------------- #
def test_patch_trim_start_beyond_saved_trim_end_rejected():
    # BUG CONFERMATO QA-08: PATCH trim_start non viene confrontato col trim_end
    # GIA' SALVATO (videos.py:149-152 valida solo contro la durata) — vedi
    # TEST_REPORT.md. Con duration=30 e trim_end=10 a DB, {"trim_start": 20}
    # oggi risponde 200 e salva trim_start=20 > trim_end=10: finestra di trim
    # vuota, stato incoerente e export destinato a fallire. Atteso: 422.
    vid = _make_video(original_name="trim.mp4", duration=30.0, trim_end=10.0)
    r = client.patch(f"/api/videos/{vid}", json={"trim_start": 20},
                     headers=_auth())
    assert r.status_code == 422, (
        f"trim_start=20 con trim_end=10 salvato accettato (HTTP {r.status_code}): "
        "il video resta con una finestra di trim vuota"
    )


# --------------------------------------------------------------------------- #
# 6. TEST ROSSO — GET /api/jobs?limit=-1
# --------------------------------------------------------------------------- #
def test_jobs_negative_limit_rejected():
    # BUG CONFERMATO QA-19: `limit` non ha validazione ge=1 (jobs.py:28-31),
    # min(limit, 200) non protegge dai negativi — vedi TEST_REPORT.md.
    # Oggi limit=-1 risponde 200: su SQLite "LIMIT -1" significa "nessun limite"
    # (bypass del cap 200); su Postgres "LIMIT -1" e' un errore SQL, quindi
    # l'endpoint diventerebbe un 500. Atteso: 422 di validazione.
    r = client.get("/api/jobs", params={"limit": -1}, headers=_auth())
    assert r.status_code == 422, (
        f"limit=-1 accettato (HTTP {r.status_code}): nessuna validazione ge=1"
    )


# --------------------------------------------------------------------------- #
# 7. meta: /api/health e /api/styles
# --------------------------------------------------------------------------- #
def test_health_open_and_shaped(monkeypatch):
    # senza auth: resta aperto (uptime-check) ma minimale, niente fingerprinting
    # (SECURITY_REPORT #8) — dettagli in test_health_endpoint.py
    anon = TestClient(app)  # client vergine: nessun cookie/bearer di sessione
    r = anon.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # ffmpeg/ffprobe forzati presenti: il job "veloce" della CI gira senza binari
    # nativi, ma qui verifichiamo la forma del payload autenticato, non l'ambiente.
    monkeypatch.setattr("app.routers.jobs.shutil.which", lambda name: f"/usr/bin/{name}")
    # con token valido: payload diagnostico completo (incluso ffprobe). Con
    # health_deep attivo (default) il payload ne aggiunge altri (db/disk/queue),
    # quindi si verifica l'inclusione dei campi di base, non l'uguaglianza stretta.
    r = anon.get("/api/health", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert {"ok", "version", "ffmpeg", "ffprobe", "whisper_model", "language"} <= set(body)
    assert body["ok"] is True
    assert isinstance(body["version"], str) and body["version"]
    assert isinstance(body["ffmpeg"], bool)
    assert isinstance(body["ffprobe"], bool)
    assert isinstance(body["whisper_model"], str)
    assert isinstance(body["language"], str) and body["language"]


def test_styles_expose_only_karaoke():
    r = client.get("/api/styles", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert [s["id"] for s in body] == ["karaoke_word"]
    assert set(body[0]) == {"id", "label", "description"}
    assert body[0]["label"] and body[0]["description"]
