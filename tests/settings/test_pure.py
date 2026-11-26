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


_MANUAL_INSTALL = ("winget", "Gyan", "apt install ffmpeg", "dnf install ffmpeg", "pacman -S ffmpeg",
                   "brew install ffmpeg", "ffmpeg_hints", "install_hints")


def test_no_manual_ffmpeg_install_instructions_remain():
    """Bileşenler paketle ya da "Onar" ile gelir; uygulamanın hiçbir yerinde elle kurma komutu kalmamalı."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    files = [p for p in (root / "kadraj").rglob("*") if p.is_file() and p.suffix in (".py", ".txt", ".md", ".json")]
    files.append(root / "packaging" / "linux" / "KURULUM.txt")
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        offenders += [f"{path.relative_to(root)}: {needle}" for needle in _MANUAL_INSTALL if needle in text]
    assert not offenders, offenders
    assert not hasattr(S, "install_hints")
