"""Preset di stile sottotitoli -> file .ass (burn-in via libass).

Il canvas ASS è fisso a 1080x1920 (PlayRes) e coincide con l'output:
le dimensioni dei preset sono quindi assolute e prevedibili.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import get_settings

PLAY_W, PLAY_H = 1080, 1920


@dataclass(frozen=True)
class StylePreset:
    id: str
    label: str
    description: str
    fontsize: int
    primary: str          # &HAABBGGRR
    outline_colour: str
    back_colour: str
    bold: int             # -1 = true, 0 = false (convenzione ASS)
    border_style: int     # 1 = bordo+ombra, 3 = box
    outline: float
    shadow: float
    alignment: int        # tastierino: 2 = basso centro, 5 = centro
    margin_v: int


STYLES: dict[str, StylePreset] = {
    "classic_white": StylePreset(
        id="classic_white", label="Bianco bordo nero",
        description="Classico: testo bianco con contorno nero, in basso.",
        fontsize=64, primary="&H00FFFFFF", outline_colour="&H00000000",
        back_colour="&H00000000", bold=-1, border_style=1,
        outline=3.5, shadow=1, alignment=2, margin_v=150,
    ),
    "classic_yellow": StylePreset(
        id="classic_yellow", label="Giallo bordo nero",
        description="Testo giallo con contorno nero, in basso.",
        fontsize=64, primary="&H0000FFFF", outline_colour="&H00000000",
        back_colour="&H00000000", bold=-1, border_style=1,
        outline=3.5, shadow=1, alignment=2, margin_v=150,
    ),
    "tiktok_big": StylePreset(
        id="tiktok_big", label="Grande centrale (TikTok)",
        description="Testo grande e bold al centro dello schermo, stile TikTok/Reels.",
        fontsize=96, primary="&H00FFFFFF", outline_colour="&H00000000",
        back_colour="&H00000000", bold=-1, border_style=1,
        outline=5.5, shadow=2, alignment=5, margin_v=0,
    ),
    "bottom_box": StylePreset(
        id="bottom_box", label="Basso classico con box",
        description="Testo bianco su box nero semitrasparente, in basso.",
        fontsize=54, primary="&H00FFFFFF", outline_colour="&H73000000",
        back_colour="&H73000000", bold=-1, border_style=3,
        outline=8, shadow=0, alignment=2, margin_v=120,
    ),
    "karaoke_word": StylePreset(
        id="karaoke_word", label="Karaoke parola evidenziata",
        description="Caption pulita: testo bianco con SOLO un bordo nero sottile "
                    "(niente ombra/box); la parola pronunciata si colora, sincronizzata.",
        # pulito: bianco + contorno nero SOTTILE e fisso, niente ombra, niente box
        # dietro (back_colour opaco ma shadow=0 -> non disegna nulla). \pos in export
        # gestisce posizione e scala.
        fontsize=92, primary="&H00FFFFFF", outline_colour="&H00000000",
        back_colour="&H00000000", bold=-1, border_style=1,
        outline=2.5, shadow=0, alignment=2, margin_v=300,
    ),
}

DEFAULT_STYLE = "karaoke_word"

KARAOKE_STYLE_ID = "karaoke_word"
_KARAOKE_ACTIVE_COLOUR = "&H0000FFFF&"  # giallo (formato ASS &HAABBGGRR&)
_KARAOKE_BASE_COLOUR = "&H00FFFFFF&"    # bianco


def hex_to_ass_colour(hex_str: str | None) -> str:
    """Converte un colore "#RRGGBB" (o "RRGGBB") nel formato colore ASS
    "&H00BBGGRR&" — attenzione: ASS usa l'ordine BGR e l'alpha 00 = opaco.
    Input assente o non valido -> giallo di default (_KARAOKE_ACTIVE_COLOUR),
    così l'evidenziazione resta retro-compatibile quando il colore manca."""
    if not isinstance(hex_str, str):
        return _KARAOKE_ACTIVE_COLOUR
    s = hex_str.strip().lstrip("#")
    if len(s) != 6:
        return _KARAOKE_ACTIVE_COLOUR
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return _KARAOKE_ACTIVE_COLOUR
    return f"&H00{b:02X}{g:02X}{r:02X}&"


def list_styles() -> list[dict]:
    # Per ora si espone SOLO il karaoke (unico stile mantenuto e ottimizzato).
    # Gli altri preset restano in STYLES per compatibilita' di export dei video
    # gia' salvati con quegli stili, ma non vengono offerti nella UI.
    return [{"id": s.id, "label": s.label, "description": s.description}
            for s in STYLES.values() if s.id == KARAOKE_STYLE_ID]


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _pos_tag(sub_pos: float | None) -> str:
    """Override ASS per posizionare il blocco testo: ancoraggio centrale (\\an5)
    su (centro-x, sub_pos*altezza). None -> nessun override (usa lo Style)."""
    if sub_pos is None:
        return ""
    y = int(round(PLAY_H * _clamp(sub_pos, 0.05, 0.95)))
    return f"{{\\an5\\pos({PLAY_W // 2},{y})}}"


def _ass_time(t: float) -> str:
    # si arrotonda PRIMA a centisecondi e poi si scompone, così un valore come
    # 59.999 riporta correttamente sui minuti (0:01:00.00) invece di produrre
    # il timestamp non canonico 0:00:60.00 (secondi >= 60).
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_text(text: str) -> str:
    # niente tag override iniettabili, newline -> \N
    return (text.replace("{", "(").replace("}", ")")
                .replace("\r", "").replace("\n", "\\N"))


def _seg_fields(seg) -> tuple[float, float, str, list | None]:
    """Normalizza un segmento: accetta sia tuple (start, end, text)
    sia dict {start, end, text, words} (words opzionale)."""
    if isinstance(seg, dict):
        return float(seg["start"]), float(seg["end"]), str(seg["text"]), seg.get("words") or None
    start, end, text = seg
    return float(start), float(end), str(text), None


def _karaoke_events(seg_start: float, seg_end: float, words: list,
                    active_colour: str = _KARAOKE_ACTIVE_COLOUR,
                    pos_tag: str = "") -> list[str]:
    """Eventi karaoke: un Dialogue per parola, con l'intera caption visibile
    e la sola parola attiva colorata (active_colour, default giallo).

    La finestra della parola i va dal suo start allo start della parola i+1
    (l'ultima fino alla fine della caption); tutto clampato in [seg_start, seg_end].
    Il testo delle parole viene escapato PRIMA di inserire i tag override.
    `pos_tag` (override \\an5\\pos) posiziona il blocco testo ed e' prepeso a ogni riga.
    """
    tokens = [_escape_text(str(w[2])) for w in words]
    events: list[str] = []
    for i, word in enumerate(words):
        w_start = float(word[0])
        w_next = float(words[i + 1][0]) if i + 1 < len(words) else float(seg_end)
        start = min(max(w_start, seg_start), seg_end)
        end = min(max(w_next, seg_start), seg_end)
        if end - start <= 0:
            continue  # finestra collassata (es. bordo di un taglio)
        text = pos_tag + " ".join(
            f"{{\\1c{active_colour}}}{tok}{{\\1c{_KARAOKE_BASE_COLOUR}}}" if j == i else tok
            for j, tok in enumerate(tokens)
        )
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}")
    return events


def build_ass(segments: list[tuple[float, float, str] | dict], style_id: str,
              font: str | None = None, karaoke_color: str | None = None,
              sub_pos: float | None = None, sub_scale: float = 1.0) -> str:
    """Genera il contenuto .ass. I segmenti possono essere tuple (start, end, text)
    o dict {start, end, text, words}; con style_id "karaoke_word" le caption
    che hanno words producono un Dialogue per parola (parola attiva colorata),
    le altre (es. editate a mano) il rendering normale.

    karaoke_color ("#RRGGBB" o None) definisce il colore dell'evidenziazione:
    se assente/invalido resta il giallo di default (retro-compatibile).
    sub_pos (0=alto..1=basso, centro del testo) posiziona il blocco; None = usa lo
    Style. sub_scale scala il font (moltiplicatore, clamp 0.5..2.5)."""
    settings = get_settings()
    preset = STYLES.get(style_id, STYLES[DEFAULT_STYLE])
    font = font or settings.sub_font
    active_colour = hex_to_ass_colour(karaoke_color)
    fontsize = max(8, int(round(preset.fontsize * _clamp(sub_scale, 0.5, 2.5))))
    spacing = round(max(0.0, settings.sub_spacing), 2)
    pos_tag = _pos_tag(sub_pos)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_W}
PlayResY: {PLAY_H}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fontsize},{preset.primary},&H000000FF,{preset.outline_colour},{preset.back_colour},{preset.bold},0,0,0,100,100,{spacing},0,{preset.border_style},{preset.outline},{preset.shadow},{preset.alignment},60,60,{preset.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    for seg in segments:
        start, end, text, words = _seg_fields(seg)
        if style_id == KARAOKE_STYLE_ID and words:
            lines.extend(_karaoke_events(start, end, words, active_colour=active_colour,
                                         pos_tag=pos_tag))
        else:
            lines.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,"
                f"{pos_tag}{_escape_text(text)}"
            )
    return header + "\n".join(lines) + "\n"
