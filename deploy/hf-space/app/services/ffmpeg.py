"""Orchestrazione FFmpeg: probe, thumbnail, export.

L'export è UN solo comando/encoding: trim/concat dei keep-intervals,
scale+crop a 9:16, burn-in .ass, x264+aac faststart.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

from ..config import get_settings


class FFmpegError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def probe(path: str | Path) -> dict:
    """Metadata via ffprobe: duration, width, height, fps, has_audio."""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe fallito: {proc.stderr.strip()[:300]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise FFmpegError(f"output ffprobe non interpretabile: {str(e)[:200]}")

    vstream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not vstream:
        raise FFmpegError("Nessuna traccia video nel file")
    astream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

    duration = float(data.get("format", {}).get("duration") or vstream.get("duration") or 0)
    if duration <= 0:
        raise FFmpegError("Durata non rilevabile")

    fps = 0.0
    rate = vstream.get("avg_frame_rate") or vstream.get("r_frame_rate") or "0/1"
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        pass

    return {
        "duration": round(duration, 3),
        "width": int(vstream.get("width") or 0),
        "height": int(vstream.get("height") or 0),
        "fps": round(fps, 3),
        "has_audio": astream is not None,
    }


def make_thumbnail(src: str | Path, dst: str | Path, at: float = 1.0) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, at):.3f}",
           "-i", str(src), "-frames:v", "1",
           "-vf", "scale=270:480:force_original_aspect_ratio=increase,crop=270:480",
           str(dst)]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise FFmpegError(f"thumbnail fallita: {proc.stderr.strip()[:300]}")


def escape_filter_path(path: str | Path) -> str:
    """Percorso dentro un filtro lavfi: slash avanti, ':' e apici escapati
    (necessario per i percorsi Windows tipo C:/...)."""
    p = str(Path(path).resolve()).replace("\\", "/")
    return p.replace(":", "\\:").replace("'", "\\'")


def _atempo_chain(speed: float) -> str:
    """atempo accetta 0.5..2.0 per istanza: fattori piu' alti si mettono in cascata
    (es. 4x -> atempo=2.0,atempo=2.0). Mantiene l'audio in sync col video accelerato."""
    parts: list[str] = []
    r = float(speed)
    if r <= 0:
        # difesa: un fattore <= 0 farebbe divergere all'infinito il while sottostante
        # (loop infinito -> worker bloccato). A monte apply_speedups filtra factor>1.
        raise FFmpegError(f"fattore di velocita' non valido: {speed}")
    while r > 2.0 + 1e-6:
        parts.append("atempo=2.0")
        r /= 2.0
    while r < 0.5 - 1e-6:
        parts.append("atempo=0.5")
        r /= 0.5
    parts.append(f"atempo={r:.6f}")
    return ",".join(parts)


def build_export_cmd(
    src: str | Path,
    dst: str | Path,
    keeps: list[tuple[float, float]] | list[tuple[float, float, float]],
    ass_path: str | Path | None,
    has_audio: bool,
    intro_zoom: bool = False,
    fps: float = 30.0,
) -> list[str]:
    s = get_settings()
    # `keeps` puo' essere una lista di (start, end) OPPURE un piano (start, end, speed).
    # Normalizziamo a piano: speed 1 = tempo reale, speed>1 = tratto accelerato.
    plan = [(float(seg[0]), float(seg[1]), (float(seg[2]) if len(seg) > 2 else 1.0))
            for seg in keeps]
    n = len(plan)
    if n == 0:
        # difesa: senza intervalli il filtergraph sarebbe 'concat=n=0' (invalido).
        # Normalmente timeline.keep_intervals solleva a monte quando non resta nulla.
        raise FFmpegError("nessun intervallo da esportare (piano vuoto)")
    total = sum((e - st) / spd for st, e, spd in plan)
    sfx = s.resolved_intro_sound() if intro_zoom else None

    parts: list[str] = []
    # micro-fade audio ai TAGLI INTERNI (non a inizio/fine video): smussa i click
    # e le "giunture brusche" senza cambiare la durata -> i sottotitoli restano
    # sincronizzati (nessun overlap, concat invariato).
    afd = 0.03
    jd = max(0.0, s.join_dip)              # dip video ai tagli grossi (0 = off)
    jd_gap = max(0.0, s.join_dip_min_gap)
    for i, (ks, ke, spd) in enumerate(plan):
        seg = ke - ks
        fast = spd > 1.0 + 1e-6
        vpts = f"setpts=(PTS-STARTPTS)/{spd:.6f}" if fast else "setpts=PTS-STARTPTS"
        vf = ""
        # dip morbida SOLO ai tagli dove il buco rimosso e' significativo (niente
        # flicker sui micro-tagli) e solo su segmenti a velocita' normale (le
        # giunture del parlato). Non cambia la durata del segmento.
        if jd > 0 and not fast and seg > 4 * jd:
            if i > 0 and (ks - plan[i - 1][1]) >= jd_gap:
                vf += f",fade=t=in:st=0:d={jd:.3f}"
            if i < n - 1 and (plan[i + 1][0] - ke) >= jd_gap:
                vf += f",fade=t=out:st={seg - jd:.3f}:d={jd:.3f}"
        parts.append(f"[0:v]trim=start={ks:.3f}:end={ke:.3f},{vpts}{vf}[v{i}]")
        if has_audio:
            apts = f",{_atempo_chain(spd)}" if fast else ""
            af = ""
            if not fast and seg > 4 * afd:
                if i > 0:
                    af += f",afade=t=in:st=0:d={afd}"
                if i < n - 1:
                    af += f",afade=t=out:st={seg - afd:.3f}:d={afd}"
            parts.append(f"[0:a]atrim=start={ks:.3f}:end={ke:.3f},"
                         f"asetpts=PTS-STARTPTS{apts}{af}[a{i}]")

    if has_audio:
        concat_in = "".join(f"[v{i}][a{i}]" for i in range(n))
        parts.append(f"{concat_in}concat=n={n}:v=1:a=1[vcat][acat]")
    else:
        concat_in = "".join(f"[v{i}]" for i in range(n))
        parts.append(f"{concat_in}concat=n={n}:v=1:a=0[vcat]")

    # --- video: scale/crop 9:16 (+ zoom d'ingresso) + sottotitoli ---
    vchain = (f"[vcat]scale={s.export_width}:{s.export_height}:"
              f"force_original_aspect_ratio=increase,"
              f"crop={s.export_width}:{s.export_height},setsar=1")
    z_intro = max(0.0, s.intro_zoom_amount) if intro_zoom else 0.0
    z_cont = max(0.0, s.smooth_zoom)
    if z_intro > 0 or z_cont > 0:
        fps_i = int(round(fps)) if 10 <= fps <= 120 else 30
        D = max(0.2, s.intro_zoom_duration)
        rate = (z_cont / total) if (z_cont > 0 and total > 0.1) else 0.0
        # z(it): drift lento continuo (maschera i tagli, look reel/commerciale) +
        # punch-in iniziale EASE-OUT (parte ingrandito e si assesta: reveal pulito,
        # non piu' il "pulse" grezzo). Le virgole in if()/pow() stanno dentro z='...'.
        z_expr = f"1+{rate:.6f}*it"
        if z_intro > 0:
            z_expr += f"+if(lt(it,{D:.3f}),{z_intro:.3f}*pow(1-it/{D:.3f},2),0)"
        vchain += (f",zoompan=z='{z_expr}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
                   f":d=1:s={s.export_width}x{s.export_height}:fps={fps_i}")
    if ass_path:
        vchain += f",ass='{escape_filter_path(ass_path)}'"
    vchain += "[vout]"
    parts.append(vchain)

    # --- audio: mix del suono d'ingresso (se richiesto e disponibile) ---
    audio_label: str | None = None
    if has_audio and sfx:
        parts.append(f"[1:a]volume={s.intro_sound_volume:.2f},apad[sfx]")
        parts.append("[acat][sfx]amix=inputs=2:duration=first,volume=2[aout]")
        audio_label = "[aout]"
    elif has_audio:
        audio_label = "[acat]"
    elif sfx:
        parts.append(f"[1:a]volume={s.intro_sound_volume:.2f},"
                     f"atrim=0:{total:.3f},apad=whole_dur={total:.3f}[aout]")
        audio_label = "[aout]"

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
    if sfx:
        cmd += ["-i", str(sfx)]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]"]
    if audio_label:
        cmd += ["-map", audio_label, "-c:a", "aac", "-b:a", s.export_audio_bitrate]
    cmd += ["-c:v", "libx264", "-preset", s.export_preset, "-crf", str(s.export_crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def export_video(
    src: str | Path,
    dst: str | Path,
    keeps: list[tuple[float, float]],
    ass_path: str | Path | None,
    has_audio: bool,
    progress_cb: Callable[[float], None] | None = None,
    intro_zoom: bool = False,
    fps: float = 30.0,
) -> None:
    """Esegue l'export leggendo il progresso live da `-progress pipe:1`.
    stderr va su file temporaneo per evitare deadlock dei pipe."""
    cmd = build_export_cmd(src, dst, keeps, ass_path, has_audio,
                           intro_zoom=intro_zoom, fps=fps)
    # total in secondi di OUTPUT (tiene conto della velocita' dei tratti accelerati)
    plan = [(float(seg[0]), float(seg[1]), (float(seg[2]) if len(seg) > 2 else 1.0))
            for seg in keeps]
    total = max(sum((e - st) / spd for st, e, spd in plan), 0.001)

    # watchdog: se ffmpeg si impianta, il worker non deve bloccarsi per sempre
    deadline = max(900.0, total * 12.0)
    killed = [False]

    def _kill():
        killed[0] = True
        proc.kill()

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as errf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, text=True)
        watchdog = threading.Timer(deadline, _kill)
        watchdog.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms=") and progress_cb:
                    try:
                        done = int(line.split("=", 1)[1]) / 1_000_000
                        # clamp in [0, 0.99]: ffmpeg puo' emettere out_time_ms
                        # negativi a inizio encoding
                        progress_cb(min(max(done / total, 0.0), 0.99))
                    except ValueError:
                        pass
            proc.wait()
        finally:
            watchdog.cancel()
        if proc.returncode != 0:
            errf.seek(0)
            tail = errf.read()[-800:]
            if killed[0]:
                raise FFmpegError(f"export interrotto: superato il tempo massimo ({int(deadline)}s)")
            raise FFmpegError(f"export fallito (codice {proc.returncode}): {tail}")

    if not Path(dst).exists() or Path(dst).stat().st_size == 0:
        raise FFmpegError("export fallito: file di output vuoto")
