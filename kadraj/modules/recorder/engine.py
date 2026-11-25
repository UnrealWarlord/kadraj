"""Kayıt motoru: ``sounddevice.InputStream`` geri çağrısı → kazanç → VU → gürültü kapısı → (yazıcı + yüksek ses analizi).

Gerçek zamanlı kural: **geri çağrı hafiftir** (kopyala, kazanç, VU, kapı) ve engellemez; diske yazma ve LUFS/true-peak
analizi ayrı bir **işçi iş parçacığında** (sınırlı kuyruk; dolarsa blok düşer ve ``dropped_blocks`` artar).
Çıktı **yalnızca ``.wav``**: ``soundfile`` ile PCM_24; başlık ~1 sn'de bir güncellenir (çökmede dosya okunabilir kalır).

Akış fabrikası enjekte edilebilir (testler/CI): ``factory(sample_rate, channels, device, blocksize, callback)`` →
``start()/stop()/close()`` yöntemli nesne. Gerçek aygıt yoksa ``SmartError`` atılır, çökme olmaz.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

import numpy as np

from ...core.contracts import AUDIO_OUTPUT_EXT, SmartError
from .devices import default_stream_factory, map_audio_error
from .dsp import (FLOOR_DB, GateParams, LevelMeter, LevelSnapshot, LoudnessMeter, LoudnessSnapshot, NoiseGate,
                  db_to_lin)

SUBTYPE = "PCM_24"
FLUSH_EVERY_S = 1.0
MAX_COLLECT_S = 30.0


@dataclass(frozen=True)
class EngineSnapshot:
    state: str = "idle"                      # idle | monitoring | recording
    recording: bool = False
    elapsed_s: float = 0.0
    levels: LevelSnapshot = field(default_factory=LevelSnapshot)
    loudness: LoudnessSnapshot = field(default_factory=LoudnessSnapshot)
    gate_state: str = "bypass"               # bypass | closed | open | hold
    gr_db: float = 0.0
    xruns: int = 0
    dropped_blocks: int = 0
    path: Optional[str] = None
    error: Optional[SmartError] = None


@dataclass(frozen=True)
class RecordingResult:
    path: Path
    frames: int
    sample_rate: int
    channels: int
    duration_s: float
    dropped_blocks: int
    xruns: int
    error: Optional[SmartError] = None


class WavWriter:
    """``soundfile`` ile PCM_24 WAV yazıcısı (yalnızca işçi iş parçacığından çağrılır)."""

    def __init__(self, path: Path, sample_rate: int, channels: int) -> None:
        if Path(path).suffix.lower() != AUDIO_OUTPUT_EXT:
            raise ValueError(f"ses çıktısı yalnızca {AUDIO_OUTPUT_EXT} olabilir: {path}")
        import soundfile as sf
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = Path(path)
        self.sr, self.ch = sample_rate, channels
        self._f = sf.SoundFile(str(path), mode="w", samplerate=sample_rate, channels=channels, subtype=SUBTYPE,
                               format="WAV")
        self.frames = 0
        self._since_flush = 0

    def write(self, block: np.ndarray) -> None:
        self._f.write(block)
        self.frames += len(block)
        self._since_flush += len(block)
        if self._since_flush >= self.sr * FLUSH_EVERY_S:
            self._f.flush()
            self._since_flush = 0

    def close(self) -> None:
        self._f.close()


class RecorderEngine:
    def __init__(self, stream_factory: Optional[Callable[..., Any]] = None, *, queue_blocks: int = 512,
                 blocksize: int = 1024) -> None:
        self._factory = stream_factory or default_stream_factory
        self._q: "queue.Queue[tuple]" = queue.Queue(maxsize=queue_blocks)
        self.blocksize = blocksize
        self.sample_rate, self.channels, self.device = 48000, 1, None
        self._gain_db = 0.0
        self._gain_lin = 1.0
        self._stream: Any = None
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._gate = NoiseGate(self.sample_rate)
        self._meter = LevelMeter(self.sample_rate, self.channels)
        self._loud = LoudnessMeter(self.sample_rate, self.channels)
        self._recording = False
        self._take_open = False                    # start_recording çağrıldı, stop_recording henüz çağrılmadı
        self._path: Optional[Path] = None
        self._frames = 0
        self._xruns = 0
        self._dropped = 0
        self._error: Optional[SmartError] = None
        self._collect: Optional[List[np.ndarray]] = None
        self._collected_frames = 0
        self._levels = LevelSnapshot()
        self._loudness = LoudnessSnapshot()

    # -- yapılandırma ------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._stream is not None

    @property
    def recording(self) -> bool:
        return self._recording

    def configure(self, *, sample_rate: Optional[int] = None, channels: Optional[int] = None,
                  device: Any = ..., input_gain_db: Optional[float] = None, gate_params: Optional[GateParams] = None,
                  gate_enabled: Optional[bool] = None) -> None:
        """Kazanç ve kapı **canlı** değişir; örnekleme hızı/kanal/aygıt akış kapalıyken değişir (açıkken ``RuntimeError``)."""
        if input_gain_db is not None:
            self._gain_db = max(-40.0, min(60.0, float(input_gain_db)))
            self._gain_lin = db_to_lin(self._gain_db)
        if gate_params is not None:
            self._gate.set_params(gate_params)
        if gate_enabled is not None:
            self._gate.set_enabled(gate_enabled)
        if sample_rate is not None or channels is not None or device is not ...:
            if self.running:
                raise RuntimeError("akış açıkken örnekleme hızı/kanal/aygıt değiştirilemez")
            if sample_rate is not None:
                self.sample_rate = int(sample_rate)
            if channels is not None:
                self.channels = max(1, int(channels))
            if device is not ...:
                self.device = device
            gp, ge = self._gate.params, self._gate.enabled
            self._gate = NoiseGate(self.sample_rate, gp, ge)

    @property
    def input_gain_db(self) -> float:
        return self._gain_db

    @property
    def gate(self) -> NoiseGate:
        return self._gate

    # -- akış ----------------------------------------------------------------------------------
    def _open(self) -> None:
        if self._stream is not None:
            return
        self._meter = LevelMeter(self.sample_rate, self.channels)
        self._loud = LoudnessMeter(self.sample_rate, self.channels)
        self._levels, self._loudness = LevelSnapshot(), LoudnessSnapshot()
        self._xruns = self._dropped = self._frames = 0
        self._error = None
        stream = None
        try:
            stream = self._factory(self.sample_rate, self.channels, self.device, self.blocksize, self._callback)
            self._worker = threading.Thread(target=self._work, name="kadraj-recorder-worker", daemon=True)
            self._worker.start()
            stream.start()
        except Exception as exc:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            self._shutdown_worker()
            raise map_audio_error(exc) from exc
        self._stream = stream

    def _shutdown_worker(self) -> None:
        if self._worker is not None:
            self._put(("exit",), block=True)
            self._worker.join(timeout=5)
            self._worker = None
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            for fn in ("stop", "close"):
                try:
                    getattr(stream, fn)()
                except Exception:
                    pass

    def start_monitoring(self, collect: bool = False) -> None:
        """Akışı açar (VU canlı); ``collect=True`` ise (mikrofon testi) sesi bellekte biriktirir (en çok 30 sn)."""
        with self._lock:
            self._collect = [] if collect else None
            self._collected_frames = 0
            self._open()

    def collected(self) -> np.ndarray:
        """Biriken test sesi (kazanç uygulanmış, kapı öncesi) — ``(frames, channels)``."""
        parts = self._collect or []
        return np.concatenate(parts, axis=0) if parts else np.zeros((0, self.channels), dtype=np.float32)

    def start_recording(self, path: Path) -> None:
        """Kaydı ``path`` (yalnız ``.wav``) dosyasına başlatır. Akış kapalıysa açılır."""
        path = Path(path)
        if path.suffix.lower() != AUDIO_OUTPUT_EXT:
            raise ValueError(f"ses çıktısı yalnızca {AUDIO_OUTPUT_EXT} olabilir: {path}")
        with self._lock:
            if self._recording:
                raise RuntimeError("zaten kayıt yapılıyor")
            self._open()
            self._loud = LoudnessMeter(self.sample_rate, self.channels)      # entegre değer take başına
            done = threading.Event()
            self._error = None
            self._put(("start", path, done), block=True)
            done.wait(5)
            if self._error is not None:
                err = self._error
                self._stop_all()
                raise err
            self._path, self._frames, self._recording, self._take_open = path, 0, True, True

    def stop_recording(self) -> RecordingResult:
        """Kaydı bitirir (kuyruktaki tüm bloklar yazıldıktan sonra) ve dosyayı kapatır; akış açık kalır."""
        with self._lock:
            if not self._take_open or self._path is None:
                raise RuntimeError("kayıt yapılmıyor")
            done = threading.Event()
            box: dict = {}
            self._put(("stop", done, box), block=True)
            done.wait(10)
            self._recording = self._take_open = False
            path, frames = self._path, int(box.get("frames", self._frames))
            return RecordingResult(path, frames, self.sample_rate, self.channels, frames / self.sample_rate,
                                   self._dropped, self._xruns, self._error)

    def flush(self, timeout: float = 2.0) -> bool:
        """Kuyruktaki tüm blokların işlenmesini bekler (testler ve ``snapshot`` kesinliği için)."""
        if self._worker is None:
            return True
        done = threading.Event()
        if not self._put(("flush", done), block=True):
            return False
        return done.wait(timeout)

    def stop(self) -> Optional[RecordingResult]:
        """Her şeyi durdurur (kayıt varsa bitirir). Tekrar çağrılabilir."""
        with self._lock:
            result = self.stop_recording() if self._take_open else None
            self._stop_all()
            return result

    def _stop_all(self) -> None:
        self._recording = self._take_open = False
        self._close_stream()
        self._shutdown_worker()

    # -- ses geri çağrısı (gerçek zamanlı iş parçacığı) --------------------------------------------------
    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if status and getattr(status, "input_overflow", False):
            self._xruns += 1
        x = np.array(indata, dtype=np.float32, copy=True)
        if x.ndim == 1:
            x = x[:, None]
        if self._gain_lin != 1.0:
            x *= self._gain_lin
            np.clip(x, -1.0, 1.0, out=x)
        self._levels = self._meter.process(x)                  # VU: kazanç sonrası, kapı öncesi
        y = self._gate.process(x)
        if not self._put(("block", y, x if self._collect is not None else None)):
            self._dropped += 1

    def _put(self, item: tuple, block: bool = False) -> bool:
        try:
            if block:
                self._q.put(item, timeout=5)
            else:
                self._q.put_nowait(item)
            return True
        except queue.Full:
            return False

    # -- işçi iş parçacığı --------------------------------------------------------------------------------
    def _work(self) -> None:
        writer: Optional[WavWriter] = None
        while True:
            item = self._q.get()
            kind = item[0]
            if kind == "exit":
                break
            try:
                if kind == "block":
                    _, y, raw = item
                    if writer is not None:
                        writer.write(y)
                        self._frames = writer.frames
                    if raw is not None and self._collect is not None and self._collected_frames < MAX_COLLECT_S * self.sample_rate:
                        self._collect.append(raw)
                        self._collected_frames += len(raw)
                    self._loudness = self._loud.process(y)
                elif kind == "start":
                    _, path, done = item
                    try:
                        writer = WavWriter(path, self.sample_rate, self.channels)
                    except Exception as exc:
                        self._error = map_audio_error(exc) if not isinstance(exc, ValueError) else \
                            SmartError("audio.bad_path", str(exc))
                    done.set()
                elif kind == "flush":
                    item[1].set()
                elif kind == "stop":
                    _, done, box = item
                    if writer is not None:
                        box["frames"] = writer.frames
                        writer.close()
                        writer = None
                    done.set()
            except Exception as exc:                            # disk dolu vb.: kaydı kapat, hatayı bildir
                from ...core.errors import translate
                self._error = translate(exc, {"source": "recorder", "path": str(self._path or "")})
                self._recording = False
                if writer is not None:
                    try:
                        writer.close()
                    except Exception:
                        pass
                    writer = None
        if writer is not None:
            writer.close()

    # -- durum -----------------------------------------------------------------------------------------------
    def snapshot(self) -> EngineSnapshot:
        state = "recording" if self._recording else ("monitoring" if self._stream is not None else "idle")
        return EngineSnapshot(state, self._recording, self._frames / self.sample_rate, self._levels, self._loudness,
                              self._gate.state, self._gate.gr_db, self._xruns, self._dropped,
                              str(self._path) if self._path and self._recording else None, self._error)
