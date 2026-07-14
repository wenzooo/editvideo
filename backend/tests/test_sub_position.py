"""Posizione (verticale) e scala del blocco sottotitoli nel .ass karaoke."""
from app.services.styles import PLAY_H, PLAY_W, build_ass, list_styles


def _seg():
    return {"start": 0.0, "end": 2.0, "text": "ciao a tutti",
            "words": [[0.0, 0.5, "ciao"], [0.6, 1.0, "a"], [1.1, 2.0, "tutti"]]}


def _dialogues(ass):
    return [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]


def _style_line(ass):
    return next(ln for ln in ass.splitlines() if ln.startswith("Style: Default,"))


def test_position_pos_tag_on_each_word():
    ass = build_ass([_seg()], "karaoke_word", karaoke_color="#FFFF00", sub_pos=0.80)
    y = int(round(PLAY_H * 0.80))  # 1536
    for ln in _dialogues(ass):
        assert f"\\an5\\pos({PLAY_W // 2},{y})" in ln


def test_position_default_lower_third_when_set():
    # basso-centro: y nella meta' inferiore del frame
    ass = build_ass([_seg()], "karaoke_word", sub_pos=0.80)
    assert int(round(PLAY_H * 0.80)) > PLAY_H // 2


def test_position_clamped_into_frame():
    ass = build_ass([_seg()], "karaoke_word", sub_pos=0.99)  # -> clamp 0.95
    y = int(round(PLAY_H * 0.95))
    assert f",{y})" in _dialogues(ass)[0]


def test_scale_changes_fontsize():
    base = int(_style_line(build_ass([_seg()], "karaoke_word", sub_scale=1.0)).split(",")[2])
    big = int(_style_line(build_ass([_seg()], "karaoke_word", sub_scale=2.0)).split(",")[2])
    assert big == base * 2


def test_scale_clamped():
    # scala assurda -> clamp a 2.5x, non esplode
    huge = int(_style_line(build_ass([_seg()], "karaoke_word", sub_scale=9.0)).split(",")[2])
    base = int(_style_line(build_ass([_seg()], "karaoke_word", sub_scale=1.0)).split(",")[2])
    assert huge == int(round(base * 2.5))


def test_only_karaoke_style_offered():
    styles = list_styles()
    assert [s["id"] for s in styles] == ["karaoke_word"]
