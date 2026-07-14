"""Export robusto con molti tagli (SCALING_REPORT #5) — tutto offline.

Copre due difese complementari:
1. servizio ffmpeg: con centinaia di segmenti il filtergraph inline supererebbe
   MAX_ARG_STRLEN; export_video lo scrive su file temporaneo e passa
   -filter_complex_script, quindi il comando resta corto. Il file .filtergraph
   viene SEMPRE rimosso (successo, errore, watchdog) — qui subprocess.Popen e'
   monkeypatchato per simulare ffmpeg senza eseguirlo davvero.
2. schema API: NESSUN cap sul numero di cuts (PATCH video e upsert template).
   La pipeline (auto-silenzi + retakes) scrive a DB liste anche > 1000 tagli e
   l'editor SPA le rimanda INTERE via PATCH / "Salva come Format": un cap
   bloccherebbe salvataggio/export di quei video. TemplateOut inoltre valida
   anche le righe gia' a DB (lettura), che non devono mai far fallire la lista.

Ambiente isolato configurato PRIMA di importare l'app (get_settings() e'
@lru_cache: stesso preambolo/setdefault degli altri moduli della suite).
"""
import io
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="ev_qa_filter_script_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")

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


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _keeps(n: int) -> list[tuple[float, float]]:
    """Piano con n segmenti da 0.5s separati da buchi (tagli interni)."""
    return [(i * 1.0, i * 1.0 + 0.5) for i in range(n)]


class _FakePopen:
    """Sostituto di subprocess.Popen: legge lo script -filter_complex_script
    NEL MOMENTO in cui 'ffmpeg' partirebbe (il file deve esistere li'), simula
    l'output di -progress su stdout e scrive/omette il file di destinazione."""

    captured: dict = {}
    returncode_to_give = 0

    def __init__(self, cmd, stdout=None, stderr=None, text=False):
        cls = type(self)
        cls.captured["cmd"] = list(cmd)
        idx = cmd.index("-filter_complex_script")
        script = Path(cmd[idx + 1])
        cls.captured["script_path"] = script
        cls.captured["script_existed"] = script.exists()
        cls.captured["graph"] = script.read_text(encoding="utf-8") if script.exists() else None
        if cls.returncode_to_give == 0:
            Path(cmd[-1]).write_bytes(b"0" * 64)  # dst non vuoto
        self.stdout = io.StringIO("out_time_ms=250000\nout_time_ms=500000\n")
        self.returncode = cls.returncode_to_give

    def wait(self):
        return self.returncode

    def kill(self):  # richiesto dal watchdog di export_video
        pass


@pytest.fixture
def fake_popen(monkeypatch):
    _FakePopen.captured = {}
    _FakePopen.returncode_to_give = 0
    monkeypatch.setattr(ffm.subprocess, "Popen", _FakePopen)
    return _FakePopen


def _auth() -> dict:
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


def _cuts_payload(n: int) -> list[dict]:
    """n tagli validi (start<end, dentro la durata 10.0 del video di test)."""
    return [{"start": round(i * 0.001, 6), "end": round(i * 0.001 + 0.005, 6)}
            for i in range(n)]


@pytest.fixture(autouse=True)
def _db_cleanup():
    """A fine test si eliminano SOLO le righe nuove: in suite completa il DB
    e' condiviso tra moduli, mai toccare righe altrui."""
    with SessionLocal() as db:
        before_videos = {vid for (vid,) in db.execute(select(Video.id))}
        before_templates = {tid for (tid,) in db.execute(select(Template.id))}
    yield
    with SessionLocal() as db:
        for (vid,) in db.execute(select(Video.id)):
            if vid not in before_videos:
                db.delete(db.get(Video, vid))
        for (tid,) in db.execute(select(Template.id)):
            if tid not in before_templates:
                db.delete(db.get(Template, tid))
        db.commit()


# --------------------------------------------------------------------------- #
# 1. con 500 tagli il comando resta corto, il graph sta nel file
# --------------------------------------------------------------------------- #
def test_500_cuts_graph_is_huge_but_cmd_stays_short(fake_popen, tmp_path):
    keeps = _keeps(500)
    graph = ffm.build_export_graph(keeps, None, has_audio=True)
    # il graph inline sarebbe enorme (ordine di grandezza di MAX_ARG_STRLEN=128KiB)
    assert len(graph) > 40_000
    assert graph.count("concat=n=500") == 1

    dst = tmp_path / "out.mp4"
    ffm.export_video("src.mp4", dst, keeps, None, True)
    cmd = fake_popen.captured["cmd"]
    # nessun graph inline: ogni argomento del comando resta corto
    assert "-filter_complex" not in cmd
    assert "-filter_complex_script" in cmd
    assert max(len(a) for a in cmd) < 1_000
    # e il graph completo e' finito nel file letto da 'ffmpeg'
    assert fake_popen.captured["graph"] == graph


def test_export_video_script_written_then_removed_on_success(fake_popen, tmp_path):
    dst = tmp_path / "out.mp4"
    calls: list[float] = []
    ffm.export_video("src.mp4", dst, [(0.0, 1.0)], None, True,
                     progress_cb=calls.append)
    script = fake_popen.captured["script_path"]
    assert script.suffix == ".filtergraph"
    assert fake_popen.captured["script_existed"] is True  # esisteva al lancio
    assert not script.exists()                            # rimosso nel finally
    assert dst.exists() and dst.stat().st_size > 0
    assert calls  # il progresso da -progress pipe:1 e' stato letto


def test_export_video_script_removed_on_failure(fake_popen, tmp_path):
    fake_popen.returncode_to_give = 1
    dst = tmp_path / "out.mp4"
    with pytest.raises(ffm.FFmpegError, match="export fallito"):
        ffm.export_video("src.mp4", dst, [(0.0, 1.0)], None, True)
    script = fake_popen.captured["script_path"]
    assert fake_popen.captured["script_existed"] is True
    assert not script.exists()  # pulizia anche in caso di errore


# --------------------------------------------------------------------------- #
# 2. nessun cap sul numero di cuts: il round-trip UI <-> pipeline deve reggere
# --------------------------------------------------------------------------- #
def test_patch_video_over_1000_cuts_roundtrip():
    """Regressione: l'editor rimanda SEMPRE l'intera lista cuts in PATCH (saveAll),
    inclusi i tagli auto-generati dalla pipeline. Con >1000 tagli il salvataggio
    (e quindi export/markReady) deve restare possibile, non fallire 422."""
    vid = _make_video()
    r = client.patch(f"/api/videos/{vid}", json={"cuts": _cuts_payload(1500)},
                     headers=_auth())
    assert r.status_code == 200
    assert len(r.json()["cuts"]) == 1500
    with SessionLocal() as db:
        assert len(db.get(Video, vid).cuts) == 1500


def test_template_over_1000_cuts_accepted():
    """Regressione: "Salva come Format" invia i cuts correnti del video (anche
    auto-generati, > 1000 su clip lunghe): l'upsert non deve rifiutarli."""
    r = client.post("/api/templates",
                    json={"name": "molti-tagli", "cuts": _cuts_payload(1001)},
                    headers=_auth())
    assert r.status_code == 200
    assert len(r.json()["cuts"]) == 1001


def test_list_templates_legacy_many_cuts_readable():
    """Regressione: una riga template gia' a DB con >1000 cuts (creata quando
    l'API non aveva limiti) non deve mandare in 500 GET /api/templates."""
    with SessionLocal() as db:
        tpl = Template(name="legacy-molti-tagli",
                       cuts=[{"start": round(i * 0.001, 6),
                              "end": round(i * 0.001 + 0.005, 6)}
                             for i in range(1200)])
        db.add(tpl)
        db.commit()
    r = client.get("/api/templates", headers=_auth())
    assert r.status_code == 200
    row = next(t for t in r.json() if t["name"] == "legacy-molti-tagli")
    assert len(row["cuts"]) == 1200
