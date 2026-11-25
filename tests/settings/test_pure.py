"""Saf yardımcılar: anahtar önizlemesi, avatar, durum ışığı, ayrıştırıcılar."""
from __future__ import annotations

from kadraj.modules.settings import service as S

from .fakes import FULL_KEY


def test_preview_shows_only_first_six_and_last_four():
    preview = S.mask_preview(FULL_KEY)
    assert preview == "sk-ant…1234"
    assert FULL_KEY not in preview and "SECRETKEY" not in preview


def test_short_or_empty_keys_are_fully_masked():
    assert S.mask_preview("abc") == "••••••••"
    assert S.mask_preview("") == "••••••••"
    assert S.mask_preview(None) == "••••••••"


def test_initials_for_avatars():
    assert S.initials("Anthropic (Claude)") == "AC"
    assert S.initials("ElevenLabs") == "E"
    assert S.initials("FLUX (Black Forest Labs)") == "FB"
    assert S.initials("") == "?"


def test_led_states():
    assert S.led_state(None) == "idle"
    assert S.led_state(True) == "ok"
    assert S.led_state(False) == "fail"
    assert S.led_state(None, checking=True) == "checking"


def test_ollama_models_parsing_is_tolerant():
    assert S.parse_ollama_models({"models": [{"name": "llama3"}, {"model": "qwen"}, {}]}) == ("llama3", "qwen")
    assert S.parse_ollama_models("bozuk") == ()
    assert S.parse_ollama_models({"models": "x"}) == ()


def test_ffmpeg_version_parsing():
    assert S.parse_ffmpeg_version("ffmpeg version 7.1.1-essentials Copyright") == "7.1.1-essentials"
    assert S.parse_ffmpeg_version("komut bulunamadı") == ""


def test_install_hints_cover_common_systems():
    hints = dict(S.install_hints())
    assert hints["Debian / Ubuntu"] == "sudo apt install ffmpeg"
    assert len(hints) == 5
