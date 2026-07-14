"""QA: orchestrazione FFmpeg (app/services/ffmpeg.py) — tutto offline.

Copre: _atempo_chain (cascata), escape_filter_path (characterization, vedi QA-09),
build_export_graph/build_export_cmd (filtergraph trim/concat, afade ai soli tagli
interni, speed, intro_zoom/zoompan, filtro ass, clamp fps, flag finali, keeps
vuoto, ramo -filter_complex_script), probe (con _run mockato) ed export_video
(con build_export_cmd mockato che lancia un piccolo script python al posto di
ffmpeg; export_video scrive comunque il filtergraph su file temporaneo).

Il CONTENUTO del graph si ottiene da build_export_graph; build_export_cmd con
filter_script=None lo incorpora inline in -filter_complex (fallback usato qui
nei test), mentre il percorso di produzione (export_video) passa sempre da
-filter_complex_script (vedi tests/test_filter_script.py).

Nessun test invoca ffmpeg/ffprobe reali: subprocess e' sempre sostituito.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Preambolo env condiviso della suite: get_settings() e' @lru_cache, il primo
# modulo importato congela le Settings. Valori IDENTICI agli altri moduli.
_TMP = tempfile.mkdtemp(prefix="ev_qa_ffmpegcmd_")
os.environ.setdefault("ADMIN_PASSWORD", "correct-horse-battery")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("MEDIA_ROOT", str(Path(_TMP) / "media"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(_TMP) / 'app.db').as_posix()}")
os.environ.setdefault("EMBEDDED_WORKER", "0")

import pytest

import app.services.ffmpeg as ffm
from app.config import get_settings
from app.services.ffmpeg import (
    FFmpegError,
    _atempo_chain,
    build_export_cmd,
    build_export_graph,
    escape_filter_path,
    probe,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fc(cmd: list[str]) -> str:
    """Estrae la stringa del filtergraph inline dal comando (fallback
    filter_script=None di build_export_cmd)."""
    return cmd[cmd.index("-filter_complex") + 1]


def _audio_parts(fc: str) -> list[str]:
    return [p for p in fc.split(";") if p.startswith("[0:a]atrim")]


def _video_parts(fc: str) -> list[str]:
    return [p for p in fc.split(";") if p.startswith("[0:v]trim")]


def _patch_probe_run(monkeypatch, payload, returncode: int = 0, stderr: str = ""):
    stdout = json.dumps(payload) if isinstance(payload, dict) else (payload or "")

    def fake_run(cmd, timeout=120):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(ffm, "_run", fake_run)


def _atempo_product(chain: str) -> float:
    return math.prod(float(p.split("=", 1)[1]) for p in chain.split(","))


# --------------------------------------------------------------------------- #
# 1. _atempo_chain: cascata per fattori fuori da 0.5..2.0
# --------------------------------------------------------------------------- #
def test_atempo_chain_in_range_single_instance():
    assert _atempo_chain(1.5) == "atempo=1.500000"


def test_atempo_chain_4x_two_instances():
    chain = _atempo_chain(4.0)
    parts = chain.split(",")
    # 4x -> due atempo a 2.0 in cascata (il residuo e' esattamente 2.0)
    assert parts == ["atempo=2.0", "atempo=2.000000"]
    assert _atempo_product(chain) == pytest.approx(4.0)


def test_atempo_chain_5x_two_halvings_plus_residual():
    chain = _atempo_chain(5.0)
    parts = chain.split(",")
    assert parts == ["atempo=2.0", "atempo=2.0", "atempo=1.250000"]
    assert _atempo_product(chain) == pytest.approx(5.0)
    # ogni istanza resta nel range accettato da atempo
    assert all(0.5 <= float(p.split("=")[1]) <= 2.0 for p in parts)


def test_atempo_chain_quarter_speed_cascades_0_5():
    chain = _atempo_chain(0.25)
    parts = chain.split(",")
    assert parts == ["atempo=0.5", "atempo=0.500000"]
    assert _atempo_product(chain) == pytest.approx(0.25)


# --------------------------------------------------------------------------- #
# 2. escape_filter_path — CHARACTERIZATION del comportamento attuale.
#    NOTA QA-09 (vedi TEST_REPORT.md): escape_filter_path escapa ':' e "'" per
#    il contesto lavfi NON quotato, ma build_export_cmd (riga ~169) avvolge il
#    risultato in apici singoli: ass='<escaped>'. Dentro gli apici ffmpeg NON
#    interpreta gli escape, quindi i backslash diventano parte del nome file e
#    un \' chiude anzitempo la stringa quotata (conflitto escaping+quoting).
#    Questi test fotografano l'output attuale della funzione, non lo correggono.
# --------------------------------------------------------------------------- #
def test_escape_filter_path_escapes_colon_characterization():
    out = escape_filter_path("/media/subs/a:b.ass")
    assert out == "/media/subs/a\\:b.ass"


def test_escape_filter_path_escapes_quote_characterization():
    out = escape_filter_path("/media/subs/l'inizio.ass")
    assert out == "/media/subs/l\\'inizio.ass"
    # composizione con il quoting di build_export_cmd (QA-09): l'argomento del
    # filtro ass diventa ass='/media/subs/l\'inizio.ass' — malformato per ffmpeg.
    cmd = build_export_cmd("src.mp4", "dst.mp4", [(0.0, 5.0)],
                           "/media/subs/l'inizio.ass", has_audio=False)
    assert ",ass='/media/subs/l\\'inizio.ass'" in _fc(cmd)


# --------------------------------------------------------------------------- #
# 3. build_export_cmd
# --------------------------------------------------------------------------- #
def test_build_export_single_keep_no_audio():
    cmd = build_export_cmd("in.mp4", "out.mp4", [(0.0, 5.0)], None, has_audio=False)
    fc = _fc(cmd)
    assert "trim=start=0.000:end=5.000" in fc
    assert "concat=n=1:v=1:a=0[vcat]" in fc
    # niente ramo audio: ne' atrim ne' mappatura/encoder audio
    assert "atrim" not in fc and "afade" not in fc
    assert cmd.count("-map") == 1
    assert cmd[cmd.index("-map") + 1] == "[vout]"
    assert "-c:a" not in cmd and "aac" not in cmd
    # un solo input (nessun sfx senza intro_zoom)
    assert cmd.count("-i") == 1
    assert cmd[cmd.index("-i") + 1] == "in.mp4"


def test_build_export_audio_afade_only_internal_cuts():
    # 3 segmenti: le afade smussano SOLO le giunture interne, non l'inizio del
    # primo segmento ne' la fine dell'ultimo.
    cmd = build_export_cmd("in.mp4", "out.mp4",
                           [(0.0, 5.0), (6.0, 10.0), (11.0, 15.0)],
                           None, has_audio=True)
    fc = _fc(cmd)
    a = _audio_parts(fc)
    assert len(a) == 3
    # primo segmento: nessuna fade-in (inizio assoluto), fade-out verso il taglio
    assert "afade=t=in" not in a[0]
    assert "afade=t=out:st=4.970:d=0.03" in a[0]
    # segmento centrale: entrambe
    assert "afade=t=in:st=0:d=0.03" in a[1]
    assert "afade=t=out:st=3.970:d=0.03" in a[1]
    # ultimo segmento: solo fade-in (fine assoluta senza fade-out)
    assert "afade=t=in:st=0:d=0.03" in a[2]
    assert "afade=t=out" not in a[2]
    # concat interleaved video/audio
    assert "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[vcat][acat]" in fc
    assert cmd[cmd.index("-map") + 1] == "[vout]"
    assert "[acat]" in cmd  # audio mappato senza sfx


def test_build_export_plan_with_speed_uses_setpts_and_atempo():
    cmd = build_export_cmd("in.mp4", "out.mp4",
                           [(0.0, 2.0, 1.0), (2.0, 6.0, 4.0)],
                           None, has_audio=True)
    fc = _fc(cmd)
    v = _video_parts(fc)
    a = _audio_parts(fc)
    assert "setpts=PTS-STARTPTS" in v[0] and "/1.0" not in v[0]
    assert "setpts=(PTS-STARTPTS)/4.000000" in v[1]
    # audio del tratto veloce: cascata atempo, nessuna afade sui tratti fast
    assert "atempo=2.0,atempo=2.000000" in a[1]
    assert "afade" not in a[1]
    # il tratto a velocita' normale non ha atempo
    assert "atempo" not in a[0]


def test_build_export_intro_zoom_adds_zoompan_and_sfx():
    s = get_settings()
    if s.intro_zoom_amount <= 0 and s.smooth_zoom <= 0:
        pytest.skip("zoom disattivato nelle Settings di questa run")
    cmd = build_export_cmd("in.mp4", "out.mp4", [(0.0, 5.0)], None,
                           has_audio=True, intro_zoom=True)
    fc = _fc(cmd)
    assert "zoompan=z='" in fc
    assert f"s={s.export_width}x{s.export_height}" in fc
    sfx = s.resolved_intro_sound()
    if sfx is not None:
        # secondo input = whoosh; volume+apad e mix con l'audio concatenato
        assert cmd.count("-i") == 2
        assert cmd[cmd.index("-i", cmd.index("-i") + 1) + 1] == str(sfx)
        assert f"[1:a]volume={s.intro_sound_volume:.2f},apad[sfx]" in fc
        assert "[acat][sfx]amix=inputs=2:duration=first" in fc
        assert "[aout]" in cmd
    else:
        assert cmd.count("-i") == 1


def test_build_export_ass_filter_after_scale_crop():
    cmd = build_export_cmd("in.mp4", "out.mp4", [(0.0, 5.0)],
                           "/media/subs/demo.ass", has_audio=False)
    fc = _fc(cmd)
    assert ",ass='/media/subs/demo.ass'" in fc
    # ordine nel vchain: scale -> crop -> ass (il burn-in avviene sul frame 9:16)
    assert fc.index("scale=") < fc.index("crop=") < fc.index(",ass='")
    vchain = next(p for p in fc.split(";") if p.startswith("[vcat]"))
    assert vchain.endswith("[vout]")
    assert ",ass='" in vchain


@pytest.mark.parametrize("bad_fps", [0.0, 500.0])
def test_build_export_fps_out_of_range_clamped_to_30_in_zoompan(bad_fps):
    s = get_settings()
    if s.intro_zoom_amount <= 0 and s.smooth_zoom <= 0:
        pytest.skip("zoom disattivato nelle Settings di questa run")
    cmd = build_export_cmd("in.mp4", "out.mp4", [(0.0, 5.0)], None,
                           has_audio=False, intro_zoom=True, fps=bad_fps)
    assert ":fps=30" in _fc(cmd)


def test_build_export_fps_in_range_kept_in_zoompan():
    s = get_settings()
    if s.intro_zoom_amount <= 0 and s.smooth_zoom <= 0:
        pytest.skip("zoom disattivato nelle Settings di questa run")
    cmd = build_export_cmd("in.mp4", "out.mp4", [(0.0, 5.0)], None,
                           has_audio=False, intro_zoom=True, fps=60.0)
    assert ":fps=60" in _fc(cmd)


def test_build_export_final_flags():
    s = get_settings()
    dst = "out/final.mp4"
    cmd = build_export_cmd("in.mp4", dst, [(0.0, 5.0)], None, has_audio=False)
    assert cmd[0] == "ffmpeg" and cmd[1] == "-y"
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-preset") + 1] == s.export_preset
    assert cmd[cmd.index("-crf") + 1] == str(s.export_crf)
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"
    assert cmd[cmd.index("-progress") + 1] == "pipe:1"
    assert "-nostats" in cmd
    assert cmd[-1] == dst


def test_build_export_empty_keeps_raises():
    # Robustezza (refine): con keeps=[] build_export_cmd solleva FFmpegError
    # invece di produrre un filtergraph invalido (concat=n=0). La difesa primaria
    # resta a monte (timeline.keep_intervals), questa e' difesa in profondita'.
    with pytest.raises(FFmpegError):
        build_export_cmd("in.mp4", "out.mp4", [], None, has_audio=False)


def test_build_export_graph_empty_keeps_raises():
    with pytest.raises(FFmpegError):
        build_export_graph([], None, has_audio=False)


def test_build_export_graph_matches_inline_filter_complex():
    # la stringa di build_export_graph e' ESATTAMENTE quella che il fallback
    # inline di build_export_cmd mette dopo -filter_complex: un'unica fonte
    # di verita' per il graph, comunque lo si trasporti.
    keeps = [(0.0, 5.0), (6.0, 10.0, 2.0)]
    graph = build_export_graph(keeps, "/media/subs/demo.ass", has_audio=True)
    cmd = build_export_cmd("in.mp4", "out.mp4", keeps, "/media/subs/demo.ass",
                           has_audio=True)
    assert _fc(cmd) == graph


def test_build_export_cmd_filter_script_replaces_inline_graph(tmp_path):
    # con filter_script il comando referenzia il FILE (-filter_complex_script)
    # e non contiene piu' il graph inline: e' cosi' che l'export evita il limite
    # MAX_ARG_STRLEN sui piani con centinaia di segmenti.
    script = tmp_path / "graph.filtergraph"
    cmd = build_export_cmd("in.mp4", "out.mp4", [(0.0, 5.0)], None,
                           has_audio=True, filter_script=script)
    assert "-filter_complex" not in cmd
    assert cmd[cmd.index("-filter_complex_script") + 1] == str(script)
    # mappature e flag finali identici al ramo inline
    assert cmd[cmd.index("-map") + 1] == "[vout]"
    assert "[acat]" in cmd
    assert cmd[-1] == "out.mp4"


# --------------------------------------------------------------------------- #
# 4. probe (ffprobe mockato via _run)
# --------------------------------------------------------------------------- #
def test_probe_parses_valid_json(monkeypatch):
    _patch_probe_run(monkeypatch, {
        "format": {"duration": "12.5"},
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080,
             "avg_frame_rate": "30000/1001"},
            {"codec_type": "audio"},
        ],
    })
    info = probe("video.mp4")
    assert info == {"duration": 12.5, "width": 1920, "height": 1080,
                    "fps": 29.97, "has_audio": True}


def test_probe_no_video_stream_raises(monkeypatch):
    _patch_probe_run(monkeypatch, {
        "format": {"duration": "5.0"},
        "streams": [{"codec_type": "audio"}],
    })
    with pytest.raises(FFmpegError, match="Nessuna traccia video"):
        probe("audio-only.mp4")


def test_probe_missing_duration_raises(monkeypatch):
    _patch_probe_run(monkeypatch, {
        "format": {},
        "streams": [{"codec_type": "video", "width": 640, "height": 480,
                     "avg_frame_rate": "30/1"}],
    })
    with pytest.raises(FFmpegError, match="Durata non rilevabile"):
        probe("no-duration.mp4")


def test_probe_zero_duration_raises(monkeypatch):
    _patch_probe_run(monkeypatch, {
        "format": {"duration": "0"},
        "streams": [{"codec_type": "video", "width": 640, "height": 480}],
    })
    with pytest.raises(FFmpegError, match="Durata non rilevabile"):
        probe("zero-duration.mp4")


def test_probe_degenerate_frame_rate_gives_fps_zero(monkeypatch):
    _patch_probe_run(monkeypatch, {
        "format": {"duration": "3.0"},
        "streams": [{"codec_type": "video", "width": 640, "height": 480,
                     "avg_frame_rate": "0/0"}],
    })
    info = probe("weird-rate.mp4")
    assert info["fps"] == 0.0
    assert info["duration"] == 3.0
    assert info["has_audio"] is False


def test_probe_nonzero_returncode_raises_with_stderr(monkeypatch):
    _patch_probe_run(monkeypatch, None, returncode=1,
                     stderr="No such file or directory: broken.mp4")
    with pytest.raises(FFmpegError, match="ffprobe fallito") as exc:
        probe("broken.mp4")
    assert "No such file or directory" in str(exc.value)


# --------------------------------------------------------------------------- #
# 5. export_video (build_export_cmd mockato: lancia uno script python)
# --------------------------------------------------------------------------- #
def _mock_export_cmd(monkeypatch, script: str) -> None:
    monkeypatch.setattr(ffm, "build_export_cmd",
                        lambda *a, **k: [sys.executable, "-c", script])


def test_export_video_progress_and_success(monkeypatch, tmp_path):
    dst = tmp_path / "out.mp4"
    script = (
        "import pathlib\n"
        "print('frame=1')\n"                       # riga ignorata dal parser
        "print('out_time_ms=1000000', flush=True)\n"
        "print('out_time_ms=2000000', flush=True)\n"
        "print('out_time_ms=3600000', flush=True)\n"
        "print('out_time_ms=oops', flush=True)\n"  # valore invalido: ignorato
        f"pathlib.Path({str(dst)!r}).write_bytes(b'0' * 64)\n"
    )
    _mock_export_cmd(monkeypatch, script)
    calls: list[float] = []
    # keeps -> total output = 4.0 s
    ffm.export_video("src.mp4", dst, [(0.0, 4.0)], None, True,
                     progress_cb=calls.append)
    assert calls == pytest.approx([0.25, 0.5, 0.9])
    assert calls == sorted(calls)              # progresso crescente
    assert all(0.0 < c <= 0.99 for c in calls)  # mai oltre il cap 0.99
    assert dst.exists() and dst.stat().st_size > 0


def test_export_video_failure_reports_stderr_tail(monkeypatch, tmp_path):
    dst = tmp_path / "out.mp4"
    script = (
        "import sys\n"
        "sys.stderr.write('kaboom-dettaglio-encoder')\n"
        "sys.exit(1)\n"
    )
    _mock_export_cmd(monkeypatch, script)
    with pytest.raises(FFmpegError, match="export fallito \\(codice 1\\)") as exc:
        ffm.export_video("src.mp4", dst, [(0.0, 4.0)], None, True)
    assert "kaboom-dettaglio-encoder" in str(exc.value)


def test_export_video_exit_zero_but_missing_output_raises(monkeypatch, tmp_path):
    dst = tmp_path / "never-written.mp4"
    _mock_export_cmd(monkeypatch, "print('out_time_ms=100000')\n")
    with pytest.raises(FFmpegError, match="output vuoto"):
        ffm.export_video("src.mp4", dst, [(0.0, 4.0)], None, True)
    assert not dst.exists()


def test_export_video_exit_zero_but_empty_output_raises(monkeypatch, tmp_path):
    dst = tmp_path / "empty.mp4"
    script = f"import pathlib; pathlib.Path({str(dst)!r}).write_bytes(b'')\n"
    _mock_export_cmd(monkeypatch, script)
    with pytest.raises(FFmpegError, match="output vuoto"):
        ffm.export_video("src.mp4", dst, [(0.0, 4.0)], None, True)
