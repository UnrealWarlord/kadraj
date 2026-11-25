"""Teleprompter durum mantığı: kelime ilerlemesi, hız ön ayarları, senkron, kalıcılık."""
from __future__ import annotations

import pytest

from kadraj.modules.recorder.teleprompter import (MAX_FONT, MAX_WPM, MIN_FONT, MIN_WPM, SPEEDS, Prompter, clamp_wpm,
                                                  parse_text, speed_for, wrap_words)

TEXT = "Merhaba ve hoş geldiniz.\nBugün size evde, bir telefon\n\nve bir mikrofonla stüdyo kalitesinde"


def test_speed_presets_match_the_spec():
    assert SPEEDS == {"slow": 110, "normal": 140, "fast": 170}
    assert speed_for(140) == "normal" and speed_for(110) == "slow" and speed_for(170) == "fast" and speed_for(123) == "custom"
    assert clamp_wpm(1) == MIN_WPM and clamp_wpm(9999) == MAX_WPM and clamp_wpm(139.6) == 140


def test_parse_text_words_and_paragraph_breaks():
    words, breaks = parse_text(TEXT)
    assert words[:3] == ["Merhaba", "ve", "hoş"] and len(words) == 14
    assert breaks == {3, 8, 13}                                                   # her satır/paragraf sonu
    assert parse_text("") == ([], set()) and parse_text("  \n \n") == ([], set())


def test_wrap_words_helper():
    lines = wrap_words(["bir", "iki", "üç", "dört"], 8)
    assert lines == [(0, 1, "bir iki"), (2, 3, "üç dört")]
    assert wrap_words([], 10) == []


@pytest.mark.parametrize("name, wpm", [("slow", 110), ("normal", 140), ("fast", 170)])
def test_advances_at_the_chosen_words_per_minute(name, wpm):
    p = Prompter("kelime " * 400)
    p.set_speed(name)
    assert p.wpm == wpm and p.speed_name == name
    p.start()
    for _ in range(600):
        p.tick(0.1)                                                               # 60 sn
    assert p.position == pytest.approx(wpm, abs=0.01)                              # 1 dakikada wpm kelime
    assert p.current_index == wpm and p.progress == pytest.approx(wpm / 400)


def test_does_not_advance_unless_running():
    p = Prompter(TEXT)
    p.tick(10)
    assert p.position == 0
    p.start()
    p.tick(1.0)
    p.pause()
    pos = p.position
    p.tick(5.0)
    assert p.position == pos and not p.running


def test_stops_at_the_end_and_restarts_from_the_top():
    p = Prompter("bir iki üç", wpm=MAX_WPM)
    p.start()
    p.tick(60)
    assert p.finished and not p.running and p.current_index == 2
    p.start()
    assert p.position == 0 and p.running


def test_empty_text_never_starts():
    p = Prompter("")
    p.start()
    assert not p.running and p.progress == 0 and p.current_index == 0 and not p.finished
    p.tick(5)
    assert p.position == 0


def test_toggle_reset_seek_and_estimates():
    p = Prompter("a b c d e f g h i j", wpm=60)
    p.toggle()
    assert p.running
    p.toggle()
    assert not p.running
    p.seek(5)
    assert p.position == 5 and p.remaining_s == pytest.approx(5.0)
    p.seek(999)
    assert p.position == 10 and p.finished
    p.seek(-3)
    assert p.position == 0
    assert p.duration_s == pytest.approx(10.0)
    p.seek(4)
    p.reset()
    assert p.position == 0 and not p.running


def test_set_text_resets_progress():
    p = Prompter(TEXT)
    p.start()
    p.tick(3)
    p.set_text("yeni metin burada")
    assert p.position == 0 and not p.running and len(p.words) == 3


def test_custom_wpm_and_font_limits():
    p = Prompter("x y z")
    p.set_speed(155)
    assert p.wpm == 155 and p.speed_name == "custom"
    p.set_speed(5)
    assert p.wpm == MIN_WPM
    p.set_font(1)
    assert p.font_px == MIN_FONT
    p.set_font(500)
    assert p.font_px == MAX_FONT
    with pytest.raises(ValueError):
        Prompter().set_speed("warp")                                                  # bilinmeyen ön ayar adı


def test_settings_roundtrip_and_garbage_tolerance():
    p = Prompter("metin burada", wpm=170, font_px=48, mirror=True, sync=False)
    q = Prompter.from_settings(p.to_settings())
    assert (q.text, q.wpm, q.font_px, q.mirror, q.sync) == ("metin burada", 170, 48, True, False)
    assert Prompter.from_settings(None).wpm == 140
    assert Prompter.from_settings({"wpm": "abc"}).wpm == 140
    assert Prompter.from_settings({"font_px": None}).font_px == 36


def test_defaults_match_the_design():
    p = Prompter()
    assert p.wpm == 140 and p.font_px == 36 and p.mirror is False and p.sync is True
