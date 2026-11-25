"""``.srt`` altyazı biçimlendirme/okuma (saf Python). Segment türü ``ai_engine.providers.whisper_local.Segment``."""
from __future__ import annotations

import os
import re
import tempfile
import textwrap
from pathlib import Path
from typing import Iterable, List

from ...ai_engine.providers.whisper_local import Segment


def format_timestamp(seconds: float) -> str:
    """``HH:MM:SS,mmm`` (negatif → 0)."""
    ms = max(0, int(round(seconds * 1000.0)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def wrap_caption(text: str, max_chars: int = 42, max_lines: int = 2) -> str:
    lines = textwrap.wrap(" ".join(text.split()), width=max_chars) or [""]
    if len(lines) > max_lines:                      # fazlasını son satıra kat (metin kaybı olmasın)
        lines = lines[:max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return "\n".join(lines)


def format_srt(segments: Iterable[Segment], max_chars: int = 42, max_lines: int = 2) -> str:
    blocks = []
    for i, seg in enumerate((s for s in segments if s.text.strip()), 1):
        end = max(seg.end, seg.start + 0.001)
        blocks.append(f"{i}\n{format_timestamp(seg.start)} --> {format_timestamp(end)}\n"
                      f"{wrap_caption(seg.text, max_chars, max_lines)}\n")
    return "\n".join(blocks)


def write_srt(path: Path, segments: Iterable[Segment]) -> Path:
    """Atomik yazar (yarım ``.srt`` kalmaz). UTF-8, BOM yok."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".srt-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(format_srt(segments))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


_TS = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{3})")


def parse_srt(text: str) -> List[Segment]:
    out: List[Segment] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            m = _TS.search(line)
            if m:
                g = [int(x) for x in m.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                out.append(Segment(start, end, " ".join(lines[i + 1:]).strip()))
                break
    return out


def count_segments(path: Path) -> int:
    try:
        return len(parse_srt(Path(path).read_text(encoding="utf-8")))
    except OSError:
        return 0
