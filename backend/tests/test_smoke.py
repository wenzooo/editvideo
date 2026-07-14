"""Smoke test end-to-end REALE della pipeline (senza Whisper, che richiede
il download del modello): genera un video di prova con FFmpeg, lo carica via
API, imposta trim + taglio + sottotitoli manuali, esegue il job di export e
verifica durata e metadata dell'output.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg assente")

TMP = Path(tempfile.mkdtemp(prefix="editvideo_test_"))
os.environ.update({
    "MEDIA_ROOT": str(TMP / "media"),
    "DATA_DIR": str(TMP / "data"),
    "DATABASE_URL": f"sqlite:///{(TMP / 'test.db').as_posix()}",
    "ADMIN_PASSWORD": "test-password",
    "EMBEDDED_WORKER": "0",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.ffmpeg import probe  # noqa: E402


def make_sample_video(path: Path, seconds: int = 10) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=540x960:rate=30",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"password": "test-password"})
        assert r.status_code == 200
        # Bearer come la SPA: il cookie di sessione ha il flag Secure
        # (COOKIE_SECURE default True) e httpx non lo rimanda su http://testserver.
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        yield c


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    p = tmp_path_factory.mktemp("src") / "sample.mp4"
    make_sample_video(p)
    return p


def test_auth_required(sample):
    with TestClient(app) as anon:
        assert anon.get("/api/videos").status_code == 401
        assert anon.post("/api/auth/login", json={"password": "sbagliata"}).status_code == 401


def test_full_pipeline(client, sample):
    # 1) upload
    with open(sample, "rb") as f:
        r = client.post("/api/videos/upload",
                        files=[("files", ("sample.mp4", f, "video/mp4"))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == []
    video = body["created"][0]
    vid = video["id"]
    assert 9.5 <= video["duration"] <= 10.5
    assert video["width"] == 540 and video["height"] == 960
    assert video["status"] == "uploaded"

    # thumbnail e stream disponibili
    assert client.get(f"/api/videos/{vid}/thumbnail").status_code == 200
    assert client.get(f"/api/videos/{vid}/file").status_code == 200

    # 2) sottotitoli manuali (simulano l'output della trascrizione, timeline originale)
    segs = [
        {"start": 0.5, "end": 2.0, "text": "Ciao a tutti!"},
        {"start": 3.2, "end": 4.5, "text": "Questa parte verrà tagliata"},
        {"start": 5.5, "end": 7.0, "text": "E qui si riparte"},
    ]
    r = client.put(f"/api/videos/{vid}/subtitles", json={"segments": segs})
    assert r.status_code == 200 and len(r.json()) == 3

    # 3) trim 1..9 + cut 3..5 + stile TikTok -> durata attesa 6s
    r = client.patch(f"/api/videos/{vid}", json={
        "trim_start": 1.0, "trim_end": 9.0,
        "cuts": [{"start": 3.0, "end": 5.0}],
        "subtitle_style": "tiktok_big",
        "status": "ready",
    })
    assert r.status_code == 200, r.text

    # 4) export: enqueue via API, esecuzione diretta del job (niente worker in test)
    r = client.post(f"/api/videos/{vid}/export")
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]

    from app.worker import run_job
    run_job(job_id)

    r = client.get(f"/api/jobs/{job_id}")
    assert r.json()["status"] == "done", r.json()

    r = client.get(f"/api/videos/{vid}")
    data = r.json()
    assert data["status"] == "exported" and data["has_export"]

    # 5) verifica dell'output: 1080x1920, ~6s
    from app.config import get_settings
    out = get_settings().exports_dir / f"{vid}.mp4"
    meta = probe(out)
    assert meta["width"] == 1080 and meta["height"] == 1920
    assert 5.5 <= meta["duration"] <= 6.6, meta
    assert meta["has_audio"]

    # 6) download
    r = client.get(f"/api/videos/{vid}/export/download")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")


def test_batch_and_validation(client, sample):
    # secondo video per il batch
    with open(sample, "rb") as f:
        r = client.post("/api/videos/upload",
                        files=[("files", ("altro.mp4", f, "video/mp4"))])
    vid = r.json()["created"][0]["id"]

    # validazioni
    assert client.patch(f"/api/videos/{vid}", json={"trim_start": 99}).status_code == 422
    assert client.patch(f"/api/videos/{vid}", json={"subtitle_style": "boh"}).status_code == 422
    assert client.patch(f"/api/videos/{vid}", json={"status": "exported"}).status_code == 422

    # batch transcribe: mette in coda i video "uploaded"
    r = client.post("/api/batch/transcribe")
    assert r.status_code == 200
    assert r.json()["enqueued"] >= 1

    # upload formato non supportato
    r = client.post("/api/videos/upload", files=[("files", ("nota.txt", b"ciao", "text/plain"))])
    assert r.json()["created"] == [] and len(r.json()["errors"]) == 1

    # delete
    r = client.delete(f"/api/videos/{vid}")
    # il video ha un job in coda (batch): 409, poi eliminabile una volta processato/errore
    assert r.status_code in (200, 409)


def test_template_flow(client, sample):
    # crea il format
    r = client.post("/api/templates", json={
        "name": "Format test", "trim_start": 1.0, "tail_trim": 1.0,
        "cuts": [{"start": 3.0, "end": 4.0}],
        "subtitle_style": "classic_yellow", "auto_transcribe": True,
    })
    assert r.status_code == 200, r.text
    tid = r.json()["id"]

    # upload con format applicato in automatico
    with open(sample, "rb") as f:
        r = client.post("/api/videos/upload", data={"template_id": tid},
                        files=[("files", ("fmt.mp4", f, "video/mp4"))])
    assert r.status_code == 200, r.text
    v = r.json()["created"][0]
    assert v["trim_start"] == pytest.approx(1.0)
    assert v["trim_end"] == pytest.approx(9.0, abs=0.3)          # durata ~10s - coda 1s
    assert v["cuts"] == [{"start": 3.0, "end": 4.0}]
    assert v["subtitle_style"] == "classic_yellow"

    # auto_transcribe: job in coda per questo video
    jobs = client.get("/api/jobs?active=true").json()
    assert any(j["video_id"] == v["id"] and j["type"] == "transcribe" for j in jobs)

    # upsert per nome: stesso id, valori aggiornati
    r = client.post("/api/templates", json={
        "name": "Format test", "trim_start": 0.5, "tail_trim": 0,
        "cuts": [], "subtitle_style": "classic_white", "auto_transcribe": False,
    })
    assert r.json()["id"] == tid
    assert any(t["id"] == tid for t in client.get("/api/templates").json())
