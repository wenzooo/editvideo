from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    settings.ensure_dirs()
    url = settings.resolved_database_url()
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401  (registra i mapper)
    Base.metadata.create_all(engine)
    _mini_migrations()


def _mini_migrations() -> None:
    """ALTER additivi per colonne nuove su DB esistenti (no Alembic nell'MVP)."""
    from sqlalchemy import text
    stmts = [
        "ALTER TABLE videos ADD COLUMN intro_zoom BOOLEAN DEFAULT 0",
        "ALTER TABLE videos ADD COLUMN auto_silence BOOLEAN DEFAULT 0",
        "ALTER TABLE videos ADD COLUMN auto_retakes BOOLEAN DEFAULT 0",
        "ALTER TABLE videos ADD COLUMN auto_export BOOLEAN DEFAULT 0",
        "ALTER TABLE templates ADD COLUMN intro_zoom BOOLEAN DEFAULT 0",
        "ALTER TABLE templates ADD COLUMN auto_silence BOOLEAN DEFAULT 0",
        "ALTER TABLE templates ADD COLUMN auto_retakes BOOLEAN DEFAULT 0",
        "ALTER TABLE templates ADD COLUMN auto_export BOOLEAN DEFAULT 0",
        "ALTER TABLE subtitle_segments ADD COLUMN words JSON",
        "ALTER TABLE videos ADD COLUMN karaoke_color TEXT",
        "ALTER TABLE templates ADD COLUMN karaoke_color TEXT",
        "ALTER TABLE videos ADD COLUMN sub_pos REAL DEFAULT 0.8",
        "ALTER TABLE videos ADD COLUMN sub_scale REAL DEFAULT 1.0",
        "ALTER TABLE videos ADD COLUMN auto_speedup BOOLEAN DEFAULT 1",
        "ALTER TABLE templates ADD COLUMN sub_pos REAL DEFAULT 0.8",
        "ALTER TABLE templates ADD COLUMN sub_scale REAL DEFAULT 1.0",
        "ALTER TABLE templates ADD COLUMN auto_speedup BOOLEAN DEFAULT 1",
        "ALTER TABLE videos ADD COLUMN speedups JSON",
    ]
    for stmt in stmts:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception:
            pass  # colonna già esistente


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
