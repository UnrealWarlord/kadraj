"""Kayıt listesi (take): ``02_Audio_WAV`` klasörünü tarar; dalga formu özeti ve ``.srt`` durumu.

Kaynak gerçek dosya sistemidir (liste yeniden tarama ile kurulur; durum ayrıca saklanmaz). Dalga formu özeti
(``waveform_peaks``) isteğe bağlı olarak ``.kadraj/cache/waveforms`` altında önbelleğe alınır.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ...core.contracts import AUDIO_OUTPUT_EXT
from . import wavio
from .mastering import MASTER_SUFFIX

TAKE_PREFIX = "Kayit_"


@dataclass(frozen=True)
class Take:
    path: Path
    name: str
    duration_s: float
    sample_rate: int
    channels: int
    size_bytes: int
    created: float
    srt_path: Optional[Path] = None
    is_master: bool = False

    @property
    def has_srt(self) -> bool:
        return self.srt_path is not None and self.srt_path.is_file()

    @property
    def master_path(self) -> Optional[Path]:
        for p in sorted(self.path.parent.glob(f"{self.path.stem}{MASTER_SUFFIX}*{AUDIO_OUTPUT_EXT}")):
            return p
        return None


def new_take_path(folder: Path, now: Optional[float] = None) -> Path:
    """``Kayit_YYYYmmdd_HHMMSS.wav`` (aynı saniyede ikinci kayıt ``_2`` alır). Uzantı **yalnızca** ``.wav``."""
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now if now is not None else time.time()))
    cand = Path(folder) / f"{TAKE_PREFIX}{stamp}{AUDIO_OUTPUT_EXT}"
    n = 2
    while cand.exists():
        cand = Path(folder) / f"{TAKE_PREFIX}{stamp}_{n}{AUDIO_OUTPUT_EXT}"
        n += 1
    return cand


def srt_for(wav: Path) -> Path:
    return wav.with_suffix(".srt")


def scan_takes(folder: Path) -> List[Take]:
    """Klasördeki ``.wav`` dosyaları (geçici ``.tmp.wav`` hariç), en yeni önce."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out: List[Take] = []
    for p in folder.iterdir():
        if p.suffix.lower() != AUDIO_OUTPUT_EXT or p.name.startswith(".") or ".tmp" in p.stem:
            continue
        try:
            info = wavio.info(p)
            st = p.stat()
        except Exception:
            continue                                               # bozuk/yarım dosya listeyi bozmaz
        srt = srt_for(p)
        out.append(Take(p, p.stem, float(info.duration), int(info.samplerate), int(info.channels), st.st_size,
                        st.st_mtime, srt if srt.is_file() else None, MASTER_SUFFIX in p.stem))
    return sorted(out, key=lambda t: t.created, reverse=True)


def waveform_peaks(path: Path, buckets: int = 240, cache_dir: Optional[Path] = None) -> List[float]:
    """Dosyayı ``buckets`` parçaya bölüp her parçanın tepe genliğini (0..1) döner. Önbellek: mtime+boyut anahtarlı."""
    import numpy as np
    path = Path(path)
    key = None
    if cache_dir is not None:
        st = path.stat()
        key = Path(cache_dir) / f"{path.stem}-{int(st.st_mtime)}-{st.st_size}-{buckets}.json"
        try:
            return [float(v) for v in json.loads(key.read_text(encoding="utf-8"))]
        except (OSError, ValueError):
            pass
    total = wavio.info(path).frames
    peaks = np.zeros(buckets)
    if total:
        per = max(1, total // buckets)
        pos = 0
        for data in wavio.iter_blocks(path, 48000):
            if pos >= total or not len(data):
                break
            data = data[:total - pos]
            mono = np.max(np.abs(data), axis=1)
            idx = (np.arange(pos, pos + len(mono)) // per).clip(max=buckets - 1)
            np.maximum.at(peaks, idx, mono)
            pos += len(mono)
    peaks = [min(1.0, float(p)) for p in peaks.tolist()]
    if key is not None:
        try:
            key.parent.mkdir(parents=True, exist_ok=True)
            key.write_text(json.dumps(peaks), encoding="utf-8")
        except OSError:
            pass
    return peaks
