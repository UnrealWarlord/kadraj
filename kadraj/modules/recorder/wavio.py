"""``.wav`` okuma/yazma: masaüstünde ``soundfile`` (libsndfile), yoksa (Android) standart ``wave`` + numpy.

Android'de libsndfile yok (``soundfile`` paketlenmez). Kayıt yine **yalnız ``.wav``, PCM 24 bit** olur; bu modülün
yazıcısı başlığı ~1 sn'de bir günceller (``soundfile`` yolundaki gibi: uygulama kapanırsa dosya okunabilir kalır).
Okuma tarafı (kayıt listesi, dalga formu, mastering) aynı küçük arayüzü kullanır:

* ``info(path)`` → ``WavInfo`` (örnekleme hızı, kanal, kare sayısı, süre)
* ``iter_blocks(path, frames)`` → ``float32`` ``(kare, kanal)`` bloklar
* ``open_writer(path, rate, channels)`` → ``write(blok)`` / ``flush()`` / ``close()``

Masaüstünde davranış değişmez: ``soundfile`` varsa her şey onunla yapılır. ``KADRAJ_WAV_BACKEND=wave`` yedek yolu
zorlar (testler).
"""
from __future__ import annotations

import os
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

import numpy as np

#: PCM 24 bit tam ölçek (libsndfile'ın float → PCM_24 ölçeğiyle aynı)
_PCM24_SCALE = 8388607.0
#: Başlık güncelleme aralığı (saniye) — ``engine.FLUSH_EVERY_S`` ile aynı
FLUSH_EVERY_S = 1.0


@dataclass(frozen=True)
class WavInfo:
    samplerate: int
    channels: int
    frames: int

    @property
    def duration(self) -> float:
        return self.frames / self.samplerate if self.samplerate else 0.0


def soundfile_module() -> Optional[Any]:
    """``soundfile`` (libsndfile ile birlikte) kullanılabiliyorsa modül, değilse ``None``."""
    if os.environ.get("KADRAJ_WAV_BACKEND", "").strip().lower() == "wave":
        return None
    try:
        import soundfile
        return soundfile
    except (ImportError, OSError):                 # paket yok ya da libsndfile yüklenemedi
        return None


# -- yazma ------------------------------------------------------------------------------------------------------------
def float_to_pcm24(block: np.ndarray) -> bytes:
    """``float32`` ``(kare, kanal)`` → iç içe (interleaved) küçük-endian 24 bit PCM baytları."""
    x = np.clip(np.asarray(block, dtype=np.float32), -1.0, 1.0)
    ints = np.rint(x.astype(np.float64) * _PCM24_SCALE).astype("<i4")
    return ints.reshape(-1).view(np.uint8).reshape(-1, 4)[:, :3].tobytes()


class Pcm24WavWriter:
    """Standart kütüphaneyle PCM 24 bit WAV yazıcısı (``soundfile`` yoksa). Başlık ``flush``'ta güncellenir."""

    def __init__(self, path: Path, sample_rate: int, channels: int) -> None:
        self.path = Path(path)
        self.sr, self.ch = int(sample_rate), int(channels)
        self._fh = open(self.path, "wb")
        self._data_bytes = 0
        self._write_header()

    def _header(self) -> bytes:
        align = self.ch * 3
        pad = self._data_bytes & 1
        return struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + self._data_bytes + pad, b"WAVE", b"fmt ", 16, 1,
                           self.ch, self.sr, self.sr * align, align, 24, b"data", self._data_bytes)

    def _write_header(self) -> None:
        pos = self._fh.tell()
        self._fh.seek(0)
        self._fh.write(self._header())
        self._fh.seek(max(pos, 44))

    def write(self, block: np.ndarray) -> None:
        data = float_to_pcm24(block)
        self._fh.write(data)
        self._data_bytes += len(data)

    def flush(self) -> None:
        self._write_header()
        self._fh.flush()

    def close(self) -> None:
        if self._fh.closed:
            return
        if self._data_bytes & 1:                   # RIFF: tek uzunluklu parçanın sonuna dolgu baytı
            self._fh.seek(44 + self._data_bytes)
            self._fh.write(b"\x00")
        self._write_header()
        self._fh.close()


def open_writer(path: Path, sample_rate: int, channels: int) -> Any:
    """PCM 24 bit ``.wav`` yazıcısı: ``soundfile`` varsa onunla, yoksa ``Pcm24WavWriter``."""
    sf = soundfile_module()
    if sf is not None:
        return sf.SoundFile(str(path), mode="w", samplerate=sample_rate, channels=channels, subtype="PCM_24",
                            format="WAV")
    return Pcm24WavWriter(path, sample_rate, channels)


# -- okuma ------------------------------------------------------------------------------------------------------------
def info(path: Path) -> WavInfo:
    """Dosyanın biçim bilgisi. Okunamazsa ``soundfile``/``wave`` istisnası (çağıran yakalar)."""
    sf = soundfile_module()
    if sf is not None:
        i = sf.info(str(path))
        return WavInfo(int(i.samplerate), int(i.channels), int(i.frames))
    with wave.open(str(path), "rb") as w:
        return WavInfo(w.getframerate(), w.getnchannels(), w.getnframes())


def _ints_to_float(raw: bytes, width: int, channels: int) -> np.ndarray:
    if width == 1:                                 # 8 bit WAV işaretsizdir
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        x = ints.astype(np.float32) / 8388608.0
    elif width == 4:
        x = (np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0).astype(np.float32)
    else:
        raise wave.Error(f"desteklenmeyen örnek genişliği: {width} bayt")
    return x.reshape(-1, channels)


def iter_blocks(path: Path, frames: int = 48000) -> Iterator[np.ndarray]:
    """Dosyayı ``frames`` karelik ``float32`` ``(kare, kanal)`` bloklar halinde okur."""
    sf = soundfile_module()
    if sf is not None:
        with sf.SoundFile(str(path)) as f:
            while True:
                data = f.read(frames, dtype="float32", always_2d=True)
                if not len(data):
                    return
                yield data
    with wave.open(str(path), "rb") as w:
        width, ch = w.getsampwidth(), w.getnchannels()
        while True:
            raw = w.readframes(frames)
            if not raw:
                return
            yield _ints_to_float(raw[:len(raw) - len(raw) % (width * ch)], width, ch)
