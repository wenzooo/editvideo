"""QA: parsing dell'output di silencedetect e classificazione dei silenzi
(app/services/silence.py).

Modulo di servizio puro: nessun import di app.main/app.db, quindi nessun
preambolo env. ffmpeg NON viene mai eseguito: subprocess.run e' monkeypatchato
nel modulo app.services.silence, i test girano offline.

NON duplica la copertura esistente:
- silences_to_cuts base (pausa interna/iniziale/finale, min_cut, leave=0)
  e' gia' in test_validation.py;
- silences_to_cuts_and_speedups base (silenzio lungo -> speedup, corto -> cut,
  bordo -> cut, do_speedup=False) e' gia' in test_speedup.py.
Qui: detect_silences (parsing stderr, clamp, silenzio finale aperto, errore
ffmpeg), i rami di silences_to_cuts_and_speedups non coperti altrove e il
wiring di auto_cuts_for.
"""
import pytest

from app.services import silence


# --------------------------------------------------------------------------- #
# helper: subprocess.run finto nel modulo app.services.silence
# --------------------------------------------------------------------------- #
class _FakeProc:
    def __init__(self, stderr: str, returncode: int = 0):
        self.stderr = stderr
        self.stdout = ""
        self.returncode = returncode


def _patch_run(monkeypatch, stderr: str, returncode: int = 0):
    """Sostituisce subprocess.run visto da app.services.silence; ritorna la
    lista dei cmd con cui e' stato invocato."""
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return _FakeProc(stderr, returncode)

    monkeypatch.setattr(silence.subprocess, "run", fake_run)
    return calls


# stderr realistico di "ffmpeg ... -af silencedetect ... -f null -"
_STDERR_PAIRS = """\
ffmpeg version 6.1.1 Copyright (c) 2000-2023 the FFmpeg developers
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'input.mp4':
  Duration: 00:00:30.00, start: 0.000000, bitrate: 1043 kb/s
Output #0, null, to 'pipe:':
[silencedetect @ 0x5600a1b2c3d0] silence_start: 3.2
[silencedetect @ 0x5600a1b2c3d0] silence_end: 5.6 | silence_duration: 2.4
[silencedetect @ 0x5600a1b2c3d0] silence_start: 10.5
[silencedetect @ 0x5600a1b2c3d0] silence_end: 12.25 | silence_duration: 1.75
size=N/A time=00:00:30.00 bitrate=N/A speed= 312x
"""

_STDERR_NEGATIVE_START = """\
Output #0, null, to 'pipe:':
[silencedetect @ 0x7f3e9c] silence_start: -0.01
[silencedetect @ 0x7f3e9c] silence_end: 1.5 | silence_duration: 1.51
size=N/A time=00:00:10.00 bitrate=N/A speed= 400x
"""

_STDERR_TRAILING_OPEN = """\
Output #0, null, to 'pipe:':
[silencedetect @ 0x55aa11] silence_start: 3.2
[silencedetect @ 0x55aa11] silence_end: 5.6 | silence_duration: 2.4
[silencedetect @ 0x55aa11] silence_start: 27.0
size=N/A time=00:00:30.00 bitrate=N/A speed= 280x
"""

_STDERR_FFMPEG_ERROR = """\
ffmpeg version 6.1.1 Copyright (c) 2000-2023 the FFmpeg developers
[mov,mp4,m4a,3gp,3g2,mj2 @ 0x5566aa] moov atom not found
input.mp4: Invalid data found when processing input
"""


# --------------------------------------------------------------------------- #
# 1. detect_silences: parsing dello stderr di silencedetect
# --------------------------------------------------------------------------- #
def test_detect_silences_parses_complete_pairs(monkeypatch):
    calls = _patch_run(monkeypatch, _STDERR_PAIRS)
    got = silence.detect_silences("input.mp4", noise_db=-40.0, min_dur=0.25)
    assert got == [(3.2, 5.6), (10.5, 12.25)]
    # il comando ffmpeg viene costruito con i parametri richiesti
    (cmd,) = calls
    assert cmd[0] == "ffmpeg"
    assert "input.mp4" in cmd
    assert cmd[cmd.index("-af") + 1] == "silencedetect=noise=-40.0dB:d=0.25"


def test_detect_silences_clamps_negative_start_to_zero(monkeypatch):
    # silencedetect puo' emettere silence_start leggermente negativo (-0.01)
    _patch_run(monkeypatch, _STDERR_NEGATIVE_START)
    assert silence.detect_silences("x.mp4") == [(0.0, 1.5)]


def test_detect_silences_trailing_silence_has_none_end(monkeypatch):
    # il file termina in silenzio: l'ultimo silence_start resta senza
    # silence_end -> la fine e' None (sara' la durata a chiuderlo a valle)
    _patch_run(monkeypatch, _STDERR_TRAILING_OPEN)
    assert silence.detect_silences("x.mp4") == [(3.2, 5.6), (27.0, None)]


def test_detect_silences_empty_stderr_returns_empty(monkeypatch):
    _patch_run(monkeypatch, "")
    assert silence.detect_silences("x.mp4") == []


# --------------------------------------------------------------------------- #
# 2. detect_silences: errore di ffmpeg (returncode != 0)
# --------------------------------------------------------------------------- #
# QA-07 CORRETTO: detect_silences ora controlla proc.returncode e solleva
# RuntimeError con la coda dello stderr, invece di ritornare [] in silenzio
# (che era indistinguibile da "nessuna pausa"). Il chiamante
# (worker.run_transcribe) ha gia' il try/except che logga e prosegue.
def test_detect_silences_ffmpeg_failure_raises_runtime_error(monkeypatch):
    _patch_run(monkeypatch, _STDERR_FFMPEG_ERROR, returncode=1)
    with pytest.raises(RuntimeError) as exc:
        silence.detect_silences("corrotto.mp4")
    msg = str(exc.value)
    assert "returncode=1" in msg
    # la coda dello stderr di ffmpeg finisce nel messaggio (diagnosi tracciata)
    assert "Invalid data found when processing input" in msg


def test_detect_silences_ffmpeg_failure_message_tail_is_bounded(monkeypatch):
    # stderr molto lungo: nel messaggio entra solo la coda (~300 char)
    long_stderr = "x" * 5000 + "\nCODA-FINALE-ERRORE"
    _patch_run(monkeypatch, long_stderr, returncode=234)
    with pytest.raises(RuntimeError) as exc:
        silence.detect_silences("corrotto.mp4")
    msg = str(exc.value)
    assert "CODA-FINALE-ERRORE" in msg
    assert "returncode=234" in msg
    assert len(msg) < 400  # prefisso + coda limitata a 300 char


# --------------------------------------------------------------------------- #
# 3. silences_to_cuts_and_speedups: rami non coperti da test_speedup.py
# --------------------------------------------------------------------------- #
def test_gap_over_speedup_min_but_too_narrow_after_edges_falls_back_to_cut():
    # (a) gap (1.0s) >= speedup_min (1.0) MA dopo aver tolto speedup_edge=0.4
    # per lato resta b-a = 0.2 < 0.3 -> niente speedup, fallback a TAGLIO
    cuts, sp = silence.silences_to_cuts_and_speedups(
        [(5.0, 6.0)], duration=30.0,
        speedup_min=1.0, speedup_edge=0.4, speedup_factor=4.0)
    assert sp == []
    assert cuts == [{"start": 5.12, "end": 5.88}]  # leave default 0.24


def test_both_disabled_returns_nothing():
    # (b) do_cut=False e do_speedup=False: nessuna azione, qualunque silenzio
    cuts, sp = silence.silences_to_cuts_and_speedups(
        [(3.0, 3.8), (10.0, 16.0)], duration=30.0,
        do_cut=False, do_speedup=False)
    assert cuts == []
    assert sp == []


def test_do_cut_false_short_gap_yields_neither_cut_nor_speedup():
    # (c) gap corto (0.8 < speedup_min) e do_cut=False: il silenzio non
    # qualifica per lo speedup e il taglio e' disabilitato -> nulla
    cuts, sp = silence.silences_to_cuts_and_speedups(
        [(3.0, 3.8)], duration=30.0, do_cut=False, speedup_min=2.5)
    assert cuts == []
    assert sp == []


def test_speedup_factor_not_greater_than_one_falls_back_to_cut():
    # (d) factor <= 1.0 non accelera nulla: il silenzio lungo torna TAGLIO
    cuts, sp = silence.silences_to_cuts_and_speedups(
        [(10.0, 16.0)], duration=30.0, speedup_min=2.5, speedup_factor=1.0)
    assert sp == []
    assert cuts == [{"start": 10.12, "end": 15.88}]


def test_edge_silences_start_and_open_end_are_cut_flush():
    # (e) silenzio iniziale + silenzio finale APERTO (e=None, chiuso dalla
    # durata): entrambi ai bordi -> tagli a filo, mai speedup anche se il
    # finale e' lungo (5s >= speedup_min)
    cuts, sp = silence.silences_to_cuts_and_speedups(
        [(0.0, 2.0), (25.0, None)], duration=30.0,
        speedup_min=2.5, speedup_factor=4.0)
    assert sp == []
    assert cuts == [
        {"start": 0.0, "end": 1.88},    # a filo dell'inizio, respiro interno
        {"start": 25.12, "end": 30.0},  # respiro interno, a filo della fine
    ]


# --------------------------------------------------------------------------- #
# 4. auto_cuts_for: wiring detect_silences -> silences_to_cuts
# --------------------------------------------------------------------------- #
def test_auto_cuts_for_forwards_detection_params_and_leave(monkeypatch):
    seen = {}

    def fake_detect(path, noise_db=-35.0, min_dur=0.4):
        seen["path"] = str(path)
        seen["noise_db"] = noise_db
        seen["min_dur"] = min_dur
        return [(3.0, 5.0)]

    monkeypatch.setattr(silence, "detect_silences", fake_detect)
    cuts = silence.auto_cuts_for("video.mp4", 10.0,
                                 noise_db=-42.0, min_dur=0.6, leave=0.4)
    assert seen == {"path": "video.mp4", "noise_db": -42.0, "min_dur": 0.6}
    # leave=0.4 arriva a silences_to_cuts: half=0.2 -> [3.2, 4.8]
    assert cuts == [{"start": 3.2, "end": 4.8}]


def test_auto_cuts_for_builds_silencedetect_command(monkeypatch):
    # wiring completo fino al comando ffmpeg (subprocess mockato)
    calls = _patch_run(monkeypatch, "")
    cuts = silence.auto_cuts_for("clip.mp4", 20.0, noise_db=-40.0, min_dur=0.25)
    assert cuts == []
    (cmd,) = calls
    assert cmd[0] == "ffmpeg"
    assert "clip.mp4" in cmd
    assert cmd[cmd.index("-af") + 1] == "silencedetect=noise=-40.0dB:d=0.25"
    assert cmd[-2:] == ["-f", "null"] or cmd[-3:-1] == ["-f", "null"]
