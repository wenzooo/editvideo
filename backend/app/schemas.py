from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Colore evidenziazione karaoke: "#RRGGBB" oppure "RRGGBB" (cancelletto opzionale).
_HEX_COLOUR_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def _normalize_karaoke_color(v):
    """None -> None; "#RRGGBB"/"RRGGBB" -> "#RRGGBB" (maiuscolo, cancelletto).
    Qualsiasi altro valore solleva ValueError (HTTP 422 a livello di router)."""
    if v is None:
        return v
    if not isinstance(v, str) or not _HEX_COLOUR_RE.match(v.strip()):
        raise ValueError("karaoke_color deve essere un colore esadecimale '#RRGGBB'")
    return "#" + v.strip().lstrip("#").upper()


class CutRange(BaseModel):
    # allow_inf_nan=False: Infinity/NaN passerebbero i vincoli ge/gt (inf >= 0)
    # e finirebbero nella matematica della timeline: li rifiutiamo a monte.
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: float, info):
        start = info.data.get("start")
        if start is not None and v <= start:
            raise ValueError("end deve essere > start")
        return v


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_name: str
    duration: float
    width: int
    height: int
    fps: float
    size_bytes: int
    has_audio: bool
    status: str
    error_message: str | None = None
    trim_start: float
    trim_end: float | None = None
    cuts: list[CutRange] = []
    # tratti velocizzati (silenzi lunghi): [{start, end, factor}] — solo lettura
    speedups: list = []
    subtitle_style: str
    karaoke_color: str | None = None
    sub_pos: float = 0.80
    sub_scale: float = 1.0
    intro_zoom: bool = True
    auto_silence: bool = True
    auto_retakes: bool = True
    auto_speedup: bool = True
    auto_export: bool = False
    subtitle_count: int = 0
    has_export: bool = False
    created_at: datetime
    updated_at: datetime


class UploadResult(BaseModel):
    created: list[VideoOut]
    errors: list[dict]


class VideoPatch(BaseModel):
    trim_start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    trim_end: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    clear_trim_end: bool = False
    # NESSUN cap sul numero di tagli, di proposito: l'editor SPA carica i cuts
    # del video (inclusi quelli generati dalla pipeline: auto-silenzi + retakes,
    # che su clip lunghe superano facilmente il migliaio) e in saveAll() rimanda
    # SEMPRE l'intera lista in PATCH. Un max_length qui bloccherebbe salvataggio
    # ed export di quei video (422 anche solo per RIMUOVERE un taglio). Ogni
    # elemento resta validato da CutRange e il router ricontrolla i tagli contro
    # la durata reale del video.
    cuts: list[CutRange] | None = None
    subtitle_style: str | None = None
    karaoke_color: str | None = None
    # posizione verticale (0..1) e scala del blocco sottotitoli
    sub_pos: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    sub_scale: float | None = Field(default=None, ge=0.3, le=3.0, allow_inf_nan=False)
    status: str | None = None
    intro_zoom: bool | None = None
    auto_silence: bool | None = None
    auto_retakes: bool | None = None
    auto_speedup: bool | None = None
    auto_export: bool | None = None

    @field_validator("karaoke_color")
    @classmethod
    def _valid_karaoke_color(cls, v):
        # None = "non cambiare il colore"; una stringa esplicita dev'essere "#RRGGBB"
        # (normalizzata). Input malformato -> 422 (come per subtitle_style).
        return _normalize_karaoke_color(v)

    @field_validator("subtitle_style")
    @classmethod
    def _known_style(cls, v):
        # None = "non cambiare lo stile"; uno stile esplicito deve essere un preset
        # noto. VideoOut è una classe a sé (non eredita da VideoPatch), quindi questo
        # non impatta la serializzazione dei video già salvati.
        if v is None:
            return v
        from .services.styles import STYLES
        if v not in STYLES:
            raise ValueError(f"Stile sconosciuto: {v!r}")
        return v

    @model_validator(mode="after")
    def _trim_coherent(self):
        # Coerenza trim solo quando ENTRAMBI sono forniti nella stessa richiesta e
        # non si sta azzerando trim_end. Il confronto con la durata reale del video
        # resta nel router (che è l'unico a conoscerla): qui non introduciamo vincoli
        # che dipendono da dati che lo schema non ha.
        if (not self.clear_trim_end and self.trim_start is not None
                and self.trim_end is not None and self.trim_end <= self.trim_start):
            raise ValueError("trim_end deve essere > trim_start")
        return self


class SubtitleSegmentIn(BaseModel):
    # NB: NON imponiamo end > start qui di proposito. Il router filtra i segmenti
    # con end <= start o testo vuoto (tolleranza voluta verso l'output grezzo della
    # trascrizione): un vincolo rigido qui farebbe fallire l'intera PUT per un solo
    # segmento degenere invece di scartarlo. allow_inf_nan=False resta sicuro.
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: str = ""


class SubtitleSegmentOut(SubtitleSegmentIn):
    model_config = ConfigDict(from_attributes=True)
    # Modello di OUTPUT: rispecchia i valori già a DB. Ridichiariamo start/end SENZA
    # allow_inf_nan=False (esattamente come prima di questa iterazione) così che
    # l'irrigidimento dell'INPUT non cambi la lettura di eventuali righe legacy:
    # la serializzazione dei segmenti esistenti resta identica.
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    id: int
    idx: int
    # parole con timestamp [[start, end, "parola"], ...]; None se non disponibili
    words: list | None = None


class SubtitlesReplace(BaseModel):
    segments: list[SubtitleSegmentIn]


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    video_id: str
    type: str
    status: str
    progress: float
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    video_name: str | None = None


class StyleOut(BaseModel):
    id: str
    label: str
    description: str


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    trim_start: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    tail_trim: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    # NESSUN cap sul numero di tagli, come in VideoPatch: "Salva come Format"
    # dell'editor invia i cuts correnti del video, che possono includere i tagli
    # auto-generati dalla pipeline (anche > 1000 su clip lunghe). Inoltre
    # TemplateOut eredita da questo schema e valida anche in LETTURA le righe
    # gia' a DB: un max_length manderebbe in 500 GET /api/templates per TUTTI i
    # template alla prima riga legacy oltre il cap (stesso hazard documentato
    # sotto per subtitle_style).
    cuts: list[CutRange] = []
    # subtitle_style NON è validato qui contro i preset: TemplateOut eredita da
    # TemplateIn e validerebbe anche le righe già a DB, facendo fallire la lettura
    # di eventuali template legacy con stile fuori-preset. La validazione dell'input
    # resta nel router upsert_template (HTTP 422 "Stile sconosciuto").
    subtitle_style: str = "karaoke_word"
    # colore evidenziazione karaoke ("#RRGGBB"); None = giallo di default.
    # A differenza di subtitle_style, karaoke_color è una colonna NUOVA: nessuna
    # riga legacy fuori-formato esiste, quindi il validatore (idempotente su valori
    # già normalizzati) è sicuro anche per TemplateOut, che eredita da TemplateIn.
    karaoke_color: str | None = None
    sub_pos: float = Field(default=0.80, ge=0.0, le=1.0, allow_inf_nan=False)
    sub_scale: float = Field(default=1.0, ge=0.3, le=3.0, allow_inf_nan=False)
    auto_transcribe: bool = False
    intro_zoom: bool = True
    auto_silence: bool = True
    auto_retakes: bool = True
    auto_speedup: bool = True
    auto_export: bool = False

    @field_validator("karaoke_color")
    @classmethod
    def _valid_karaoke_color(cls, v):
        return _normalize_karaoke_color(v)


class TemplateOut(TemplateIn):
    model_config = ConfigDict(from_attributes=True)
    id: str


class ApplyTemplateIn(BaseModel):
    template_id: str


class LoginIn(BaseModel):
    password: str


class BatchResult(BaseModel):
    enqueued: int
    skipped: int = 0


def video_to_out(v, subtitle_count: int | None = None) -> VideoOut:
    """Se subtitle_count è fornito, evita il lazy-load dei segmenti (lista video)."""
    out = VideoOut.model_validate(v)
    out.subtitle_count = len(v.segments) if subtitle_count is None else subtitle_count
    out.has_export = bool(v.exported_path)
    return out
