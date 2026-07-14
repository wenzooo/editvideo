"""Test aggiuntivi (iterazione 7): validazione config, logging strutturato.

Restano leggeri e offline: importano solo app.config e app.logging_conf, senza
tirare dentro fastapi/sqlalchemy/whisper. Non modificano nulla dei test esistenti.
"""
import logging

from app.config import Settings
from app.logging_conf import _ContextFilter, log_context


# --------------------------------------------------------------------------- #
# config.validate_runtime
# --------------------------------------------------------------------------- #
def test_validate_runtime_flags_bad_numeric_values():
    s = Settings(worker_concurrency=0, worker_poll_seconds=0, whisper_beam=0,
                 retake_min_match=0, export_crf=99, silence_noise_db=5.0,
                 max_upload_mb=0)
    joined = " | ".join(s.validate_runtime())
    assert "WORKER_CONCURRENCY" in joined
    assert "WORKER_POLL_SECONDS" in joined
    assert "WHISPER_BEAM" in joined
    assert "RETAKE_MIN_MATCH" in joined
    assert "EXPORT_CRF" in joined
    assert "SILENCE_NOISE_DB" in joined
    assert "MAX_UPLOAD_MB" in joined


def test_validate_runtime_clean_for_sane_values(tmp_path):
    # valori espliciti coerenti + cartelle scrivibili -> nessun avviso
    s = Settings(worker_concurrency=1, worker_poll_seconds=1.5, whisper_beam=1,
                 sub_max_chars=42, silence_min_dur=0.4, silence_leave=0.24,
                 silence_noise_db=-35.0, retake_min_match=3, retake_window=10.0,
                 retake_max_cut=20.0, export_width=1080, export_height=1920,
                 export_crf=20, max_upload_mb=2048, session_days=30,
                 media_root=tmp_path, data_dir=tmp_path)
    assert s.validate_runtime() == []


def test_validate_runtime_never_raises_on_missing_dirs():
    # cartelle inesistenti: nessuna eccezione, al massimo nessun avviso di scrittura
    s = Settings(media_root="does/not/exist/xyz", data_dir="also/missing/xyz")
    assert isinstance(s.validate_runtime(), list)


# --------------------------------------------------------------------------- #
# config.public_config: mai segreti
# --------------------------------------------------------------------------- #
def test_public_config_hides_secrets():
    s = Settings(secret_key="supersecretvalue", admin_password="hunter2pw")
    pub = s.public_config()
    assert "secret_key" not in pub and "admin_password" not in pub
    flat = repr(pub).lower()
    assert "supersecretvalue" not in flat
    assert "hunter2pw" not in flat


def test_public_config_reports_log_level():
    assert Settings(log_level="DEBUG").public_config()["log_level"] == "DEBUG"


# --------------------------------------------------------------------------- #
# logging: contesto job/video iniettato nei record
# --------------------------------------------------------------------------- #
def _record() -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, "msg", (), None)


def test_log_context_injects_short_ids():
    f = _ContextFilter()
    rec = _record()
    with log_context(job_id="abcdef123456", video_id="videoid98765"):
        assert f.filter(rec) is True
    assert rec.ctx == " [job=abcdef12 video=videoid9]"
    assert rec.job_id == "abcdef12" and rec.video_id == "videoid9"


def test_log_context_empty_outside_block():
    f = _ContextFilter()
    rec = _record()
    assert f.filter(rec) is True
    assert rec.ctx == ""
    assert rec.job_id == "-" and rec.video_id == "-"


def test_log_context_resets_on_exit():
    f = _ContextFilter()
    with log_context(job_id="jobjobjob"):
        pass
    rec = _record()
    f.filter(rec)
    assert rec.ctx == ""  # ripristinato all'uscita del blocco
