"""Teleprompter mantığı (Qt yok; durum **servistedir**, sayfa yalnızca çizer).

Metin kelimelere bölünür; ``position`` kayan nokta bir **kelime indeksidir** ve ``tick(dt)`` ile ``wpm`` hızında
ilerler (Yavaş 110 / Normal 140 / Hızlı 170 kelime/dk; Legend'de serbest WPM). Kayıtla senkron başlatma servis tarafından
yapılır (``start_recording`` aynı anda ``start`` çağırır). Satır kaydırma görünüme aittir; burada yalnızca "hangi kelime
şimdi okunuyor" bilinir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

SPEEDS: Dict[str, int] = {"slow": 110, "normal": 140, "fast": 170}
MIN_WPM, MAX_WPM = 40, 300
MIN_FONT, MAX_FONT = 18, 96
_WORD = re.compile(r"\S+")


def clamp_wpm(wpm: float) -> int:
    return int(max(MIN_WPM, min(MAX_WPM, round(float(wpm)))))


def speed_for(wpm: int) -> str:
    """Bir WPM değerinin ön ayar adı; ön ayarla eşleşmiyorsa ``"custom"``."""
    for name, v in SPEEDS.items():
        if v == wpm:
            return name
    return "custom"


def parse_text(text: str) -> Tuple[List[str], Set[int]]:
    """Metni ``(kelimeler, satır_sonu_indeksleri)``'ne çevirir; boş satırlar paragraf sonu sayılır."""
    words: List[str] = []
    breaks: Set[int] = set()
    for para in re.split(r"\n\s*\n|\n", text or ""):
        found = _WORD.findall(para)
        if found:
            words.extend(found)
            breaks.add(len(words) - 1)
    return words, breaks


def wrap_words(words: List[str], max_chars: int) -> List[Tuple[int, int, str]]:
    """Kelimeleri ``max_chars``'a kadar satırlara böler: ``[(ilk_kelime, son_kelime, metin), ...]`` (görünüm yedeği/test)."""
    lines: List[Tuple[int, int, str]] = []
    start, cur = 0, ""
    for i, w in enumerate(words):
        cand = f"{cur} {w}".strip()
        if cur and len(cand) > max_chars:
            lines.append((start, i - 1, cur))
            start, cur = i, w
        else:
            cur = cand
    if cur:
        lines.append((start, len(words) - 1, cur))
    return lines


class Prompter:
    def __init__(self, text: str = "", wpm: int = SPEEDS["normal"], font_px: int = 36, mirror: bool = False,
                 sync: bool = True) -> None:
        self.wpm = clamp_wpm(wpm)
        self.font_px = max(MIN_FONT, min(MAX_FONT, int(font_px)))
        self.mirror = bool(mirror)
        self.sync = bool(sync)                 # kayıt başlayınca birlikte başla
        self.running = False
        self.position = 0.0
        self.text = ""
        self.words: List[str] = []
        self.breaks: Set[int] = set()
        self.set_text(text)

    # -- metin ---------------------------------------------------------------------------
    def set_text(self, text: str) -> None:
        self.text = text or ""
        self.words, self.breaks = parse_text(self.text)
        self.position = 0.0
        self.running = False

    # -- hız ------------------------------------------------------------------------------
    def set_speed(self, speed: "str | int") -> None:
        self.wpm = SPEEDS[speed] if isinstance(speed, str) and speed in SPEEDS else clamp_wpm(float(speed))

    @property
    def speed_name(self) -> str:
        return speed_for(self.wpm)

    def set_font(self, px: int) -> None:
        self.font_px = max(MIN_FONT, min(MAX_FONT, int(px)))

    # -- akış -------------------------------------------------------------------------------
    @property
    def finished(self) -> bool:
        return bool(self.words) and self.position >= len(self.words) - 1e-9

    def start(self) -> None:
        if self.words:
            if self.finished:
                self.position = 0.0
            self.running = True

    def pause(self) -> None:
        self.running = False

    def toggle(self) -> None:
        self.pause() if self.running else self.start()

    def reset(self) -> None:
        self.position, self.running = 0.0, False

    def seek(self, word_index: float) -> None:
        self.position = max(0.0, min(float(len(self.words)), float(word_index)))

    def tick(self, dt: float) -> float:
        """``dt`` saniye ilerletir (yalnızca çalışırken). Metin bitince durur. Yeni ``position``'ı döner."""
        if self.running and self.words and dt > 0:
            self.position += self.wpm / 60.0 * dt
            if self.position >= len(self.words):
                self.position, self.running = float(len(self.words)), False
        return self.position

    @property
    def current_index(self) -> int:
        """Şu an okunan kelimenin indeksi (metin boşsa 0)."""
        return min(int(self.position + 1e-6), max(0, len(self.words) - 1))      # kayan nokta birikimi bir kelime geri düşürmesin

    @property
    def progress(self) -> float:
        return 0.0 if not self.words else min(1.0, self.position / len(self.words))

    @property
    def duration_s(self) -> float:
        return len(self.words) / self.wpm * 60.0

    @property
    def remaining_s(self) -> float:
        return max(0.0, (len(self.words) - self.position) / self.wpm * 60.0)

    # -- kalıcılık ----------------------------------------------------------------------------
    def to_settings(self) -> Dict[str, Any]:
        return {"text": self.text, "wpm": self.wpm, "font_px": self.font_px, "mirror": self.mirror, "sync": self.sync}

    @classmethod
    def from_settings(cls, data: Optional[Mapping[str, Any]]) -> "Prompter":
        d = dict(data or {})
        try:
            return cls(str(d.get("text", "")), int(d.get("wpm", SPEEDS["normal"])), int(d.get("font_px", 36)),
                       bool(d.get("mirror", False)), bool(d.get("sync", True)))
        except (TypeError, ValueError):
            return cls()
