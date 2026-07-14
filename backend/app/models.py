from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return uuid.uuid4().hex


class VideoStatus:
    UPLOADED = "uploaded"          # caricato
    TRANSCRIBING = "transcribing"  # in elaborazione (sottotitoli)
    REVIEW = "review"              # da controllare
    READY = "ready"                # pronto per l'export
    EXPORTING = "exporting"        # in export
    EXPORTED = "exported"          # esportato
    ERROR = "error"

    ALL = [UPLOADED, TRANSCRIBING, REVIEW, READY, EXPORTING, EXPORTED, ERROR]
    # stati impostabili manualmente dall'utente (gli altri li gestisce il worker)
    USER_SETTABLE = [UPLOADED, REVIEW, READY]
    # stati in cui il video è "occupato" e non modificabile
    BUSY = [TRANSCRIBING, EXPORTING]


class JobType:
    TRANSCRIBE = "transcribe"
    EXPORT = "export"
    ALL = [TRANSCRIBE, EXPORT]


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELED = "canceled"      # annullato: dequeue logico (era 'queued') o esito annullato
    CANCELING = "canceling"    # annullamento richiesto su un job già 'running'
    ACTIVE = [QUEUED, RUNNING]
    # elenco documentato di tutti gli stati possibili di un job
    ALL = [QUEUED, RUNNING, DONE, ERROR, CANCELED, CANCELING]


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(Text)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    exported_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    # BigInteger: su Postgres INTEGER e' int32 (max ~2.1 GB) e il default
    # MAX_UPLOAD_MB=2048 lo supera; su SQLite e' comunque a 64 bit (no-op)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=True)

    status: Mapped[str] = mapped_column(String(20), default=VideoStatus.UPLOADED, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    trim_start: Mapped[float] = mapped_column(Float, default=0.0)
    trim_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    cuts: Mapped[list] = mapped_column(JSON, default=list)  # [{"start": s, "end": e}] da rimuovere
    # tratti da VELOCIZZARE (silenzi lunghi): [{"start", "end", "factor"}], timeline orig.
    speedups: Mapped[list] = mapped_column(JSON, default=list)
    subtitle_style: Mapped[str] = mapped_column(String(40), default="karaoke_word")
    # colore evidenziazione karaoke ("#RRGGBB"); NULL = giallo di default
    karaoke_color: Mapped[str | None] = mapped_column(String(7), nullable=True, default=None)
    # posizione verticale del blocco sottotitoli (frazione 0=alto..1=basso, centro
    # del testo) e scala del font (moltiplicatore). Default: basso-centro, scala 1.
    sub_pos: Mapped[float] = mapped_column(Float, default=0.80)
    sub_scale: Mapped[float] = mapped_column(Float, default=1.0)
    # automazioni ATTIVE DI DEFAULT (il prodotto deve "fare tutto da solo")
    intro_zoom: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_silence: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_retakes: Mapped[bool] = mapped_column(Boolean, default=True)
    # velocizza (invece di tagliare) i silenzi lunghi, es. apertura pacchetti
    auto_speedup: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_export: Mapped[bool] = mapped_column(Boolean, default=False)

    # indicizzato: la dashboard lista i video con ORDER BY created_at DESC
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    segments: Mapped[list["SubtitleSegment"]] = relationship(
        back_populates="video", cascade="all, delete-orphan",
        order_by="SubtitleSegment.idx", lazy="selectin",
    )
    jobs: Mapped[list["Job"]] = relationship(back_populates="video", cascade="all, delete-orphan")


class Template(Base):
    """Un "Format": schema di editing riutilizzabile (trim + tagli + stile)
    da applicare automaticamente ai video con la stessa struttura."""
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    trim_start: Mapped[float] = mapped_column(Float, default=0.0)
    tail_trim: Mapped[float] = mapped_column(Float, default=0.0)  # secondi tagliati DALLA FINE
    cuts: Mapped[list] = mapped_column(JSON, default=list)
    subtitle_style: Mapped[str] = mapped_column(String(40), default="karaoke_word")
    # colore evidenziazione karaoke ("#RRGGBB"); NULL = giallo di default
    karaoke_color: Mapped[str | None] = mapped_column(String(7), nullable=True, default=None)
    sub_pos: Mapped[float] = mapped_column(Float, default=0.80)
    sub_scale: Mapped[float] = mapped_column(Float, default=1.0)
    auto_transcribe: Mapped[bool] = mapped_column(Boolean, default=False)
    intro_zoom: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_silence: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_retakes: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_speedup: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_export: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SubtitleSegment(Base):
    __tablename__ = "subtitle_segments"
    # Indice composito (video_id, idx): copre sia il filtro/aggregazione per
    # video_id (prefisso a sinistra, es. GROUP BY video_id o DELETE WHERE video_id)
    # sia il caricamento ORDINATO dei segmenti (relationship selectin/order_by idx:
    # WHERE video_id=? ORDER BY idx). Sostituisce il singolo indice su video_id.
    __table_args__ = (
        Index("ix_subtitle_segments_video_id_idx", "video_id", "idx"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer, default=0)
    start: Mapped[float] = mapped_column(Float)   # secondi, timeline ORIGINALE
    end: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    # parole della caption con timestamp: [[start, end, "parola"], ...];
    # NULL per segmenti fallback o editati a mano (niente karaoke)
    words: Mapped[list | None] = mapped_column(JSON, nullable=True)

    video: Mapped[Video] = relationship(back_populates="segments")


class Job(Base):
    __tablename__ = "jobs"
    # Indice composito (status, created_at): serve il path piu' caldo del sistema,
    # il claim del worker in polling (WHERE status='queued' ORDER BY created_at
    # LIMIT 1) e il listing dei job attivi. Il prefisso a sinistra (status) copre
    # anche i filtri WHERE status IN (...), quindi sostituisce il singolo indice.
    __table_args__ = (
        Index("ix_jobs_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    video: Mapped[Video] = relationship(back_populates="jobs")
