"""``RecorderService`` — Ses Kayıt Stüdyosu'nun **bütün durumu** burada (sayfalar yalnızca çizer; plan §0.5).

Sorumluluklar: aygıt seçimi, örnekleme hızı (Basit sabit 48 kHz; Legend 44.1/48/88.2/96), giriş kazancı, gürültü kapısı
(Basit: yumuşak ön ayar; Legend: 5 parametre), kayıt (``RecorderEngine``), mikrofon testi / auto-gain **önerisi**,
teleprompter, kayıt listesi, yazıya çevirme ve mastering **işleri** (``ctx.jobs``).

Kurallar
--------
* Ses çıktısı **yalnız ``.wav``** (``02_Audio_WAV`` = ``folders.folder_for("wav")``); Basit 48 kHz / 24-bit sabit.
* **Hiçbir otomasyon kendiliğinden çalışmaz:** gürültü kapısı varsayılan KAPALI; auto-gain yalnızca öneri üretir
  (``apply_suggested_gain`` kullanıcı eylemidir); yazıya çevirme ve mastering yalnızca ``transcribe``/``master`` çağrılınca.
* Giriş aygıtı/PortAudio yoksa (Docker/CI) **SmartError** (``audio.no_input`` / ``audio.no_backend``), çökme yok.
* Android'de ses girişi QtMultimedia'dır (``qt_audio.py``) ve mikrofon izni kayıt/test başlamadan istenir
  (``permission.py``): izin penceresi açıkken servis "idle" kalır, izin gelince istenen iş kendiliğinden başlar.
* Ağ gerekmez (faster-whisper modeli önbellekteyse çevrimdışı çalışır; yoksa indirme hatası ``SmartError``).
"""
from __future__ import annotations

import errno
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from ...ai_engine.providers.whisper_local import TranscribeCancelled, WhisperLocalProvider
from ...core import i18n
from ...core.contracts import (AUDIO_OUTPUT_EXT, Job, JobStatus, Severity, SmartAction, SmartError, Topics, UiMode)
from ...core.errors import translate
from ...storage.folders import folder_for
from . import devices as dev
from . import permission as mic_permission
from .dsp import (SIMPLE_GATE, TARGET_DBFS, GateParams, LevelAnalysis, LevelSnapshot, LoudnessSnapshot,
                  analyze_level)
from .engine import EngineSnapshot, RecorderEngine
from .mastering import MasterCancelled, MasterSettings, find_ffmpeg, master_file
from .srt import write_srt
from .takes import Take, new_take_path, scan_takes, srt_for, waveform_peaks
from .teleprompter import Prompter

MIN_FREE_BYTES = 200 * 1024 * 1024
TICK_MS = 33
JOB_TRANSCRIBE = "recorder.transcribe"
JOB_MASTER = "recorder.master"
MIC_TEST_SECONDS = 5.0
CALIBRATION_SECONDS = 8.0


@dataclass(frozen=True)
class RecorderSnapshot:
    state: str = "idle"                         # idle | recording | testing
    elapsed_s: float = 0.0
    levels: LevelSnapshot = field(default_factory=LevelSnapshot)
    loudness: LoudnessSnapshot = field(default_factory=LoudnessSnapshot)
    gate_state: str = "bypass"
    gr_db: float = 0.0
    xruns: int = 0
    dropped_blocks: int = 0
    verdict: str = "idle"                       # idle | good | quiet | hot | clip   (canlı "Sesin harika!" çipi)
    test_remaining_s: float = 0.0
    prompter_position: float = 0.0
    prompter_running: bool = False


def smart_error_from_dict(d: Dict[str, Any]) -> SmartError:
    """``SmartError.to_dict()`` çıktısından yeniden kurar (iş kuyruğundaki ``_error`` için)."""
    sev = d.get("severity", "error")
    return SmartError(str(d.get("code", "app.unexpected")), str(d.get("title", "")), str(d.get("summary", "")),
                      cause=str(d.get("cause", "")), steps=tuple(d.get("steps", ())),
                      actions=tuple(SmartAction(a["id"], a["label"], a.get("kind", "custom"), a.get("payload", {}))
                                    for a in d.get("actions", ())),
                      severity=Severity(sev) if sev in ("info", "warning", "error") else Severity.ERROR,
                      technical=str(d.get("technical", "")), retryable=bool(d.get("retryable", False)),
                      source=str(d.get("source", "recorder")))


class _PeaksTask(QRunnable):
    def __init__(self, svc: "RecorderService", path: Path, cache: Optional[Path]) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.svc, self.path, self.cache = svc, path, cache

    def run(self) -> None:
        try:
            peaks = waveform_peaks(self.path, 240, self.cache)
        except Exception:
            peaks = []
        self.svc._peaks_done.emit(str(self.path), peaks)


class RecorderService(QObject):
    name = "recorder"

    ticked = Signal()                    # kayıt/test sürerken ~30 fps: sayfalar VU/süre/teleprompter'ı yeniler
    stateChanged = Signal(str)
    takesChanged = Signal()
    settingsChanged = Signal()
    errorRaised = Signal(object)         # SmartError (sayfa satır içi kart gösterir)
    micTestFinished = Signal(object)     # LevelAnalysis
    waveformReady = Signal(str)
    jobChanged = Signal(object)          # yazıya çevirme/mastering işi değişti (Job) — sayfalar ilerlemeyi gösterir
    _peaks_done = Signal(str, object)

    def __init__(self, ctx: Any = None, *, stream_factory: Optional[Callable[..., Any]] = None,
                 device_lister: Optional[Callable[[], List[dev.InputDevice]]] = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 min_free_bytes: int = MIN_FREE_BYTES,
                 mic: Optional[mic_permission.MicPermission] = None) -> None:
        super().__init__(ctx if isinstance(ctx, QObject) else None)
        self.ctx = ctx
        self._mic = mic or mic_permission.default()
        self._asking_permission = False
        self._injected_factory = stream_factory is not None
        self.engine = RecorderEngine(stream_factory)
        self._device_lister = device_lister or dev.list_input_devices
        self._clock, self._wall = clock, wall
        self._min_free = min_free_bytes
        self.state = "idle"
        self.last_error: Optional[SmartError] = None
        self.last_test: Optional[LevelAnalysis] = None
        self._test_deadline = 0.0
        self._rec_started = 0.0
        self._takes: List[Take] = []
        self._peaks: Dict[str, List[float]] = {}
        self._peaks_pending: set = set()
        self._devices: Optional[List[dev.InputDevice]] = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._last_tick = clock()
        self._unsubs: List[Callable[[], None]] = []
        self._peaks_done.connect(self._on_peaks)
        self.provider = WhisperLocalProvider()
        self.prompter = Prompter()
        if ctx is not None:
            self.prompter = Prompter.from_settings(ctx.settings.get("recorder.prompter"))

    # -- Service sözleşmesi ----------------------------------------------------------------------------------------
    def start(self, ctx: Any) -> None:
        self.ctx = ctx
        saved = ctx.settings.get("recorder.prompter")
        self.prompter = Prompter.from_settings(saved)
        if not saved:                                              # ilk açılış: düzenlenebilir örnek metin
            self.prompter.set_text(i18n.t("recorder.prompter.sample"))
        try:
            ctx.providers.register(self.provider, replace=True)          # gerçek yazıya çevirme sağlayıcısı
        except Exception:
            pass
        ctx.jobs.register_handler(JOB_TRANSCRIBE, self._run_transcribe)
        ctx.jobs.register_handler(JOB_MASTER, self._run_master)
        self._unsubs += [ctx.bus.subscribe(Topics.JOB_UPDATED, self._on_job_updated),
                         ctx.bus.subscribe(Topics.PROJECT_OPENED, lambda _p: self.refresh_takes()),
                         ctx.bus.subscribe(Topics.PROJECT_CLOSED, lambda _p: self.refresh_takes()),
                         ctx.bus.subscribe(Topics.MODE_CHANGED, lambda _m: self.settingsChanged.emit())]
        self.refresh_takes()

    def stop(self) -> None:
        """Uygulama kapanırken: süren kaydı düzgünce bitirir (dosya geçerli kalır), mikrofonu bırakır."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._timer.stop()
        try:
            if self.state == "recording":
                self.stop_recording()
            else:
                self.engine.stop()
        except Exception:
            pass
        self.state = "idle"
        self._pool.waitForDone(2000)

    # -- ayarlar (kalıcı; durum burada) -----------------------------------------------------------------------------------
    def _get(self, key: str, default: Any = None) -> Any:
        return self.ctx.settings.get(f"recorder.{key}", default) if self.ctx is not None else default

    def _set(self, key: str, value: Any) -> None:
        if self.ctx is not None:
            self.ctx.settings.set(f"recorder.{key}", value)
        self.settingsChanged.emit()

    @property
    def ui_mode(self) -> UiMode:
        return self.ctx.ui_mode if self.ctx is not None else UiMode.SIMPLE

    @property
    def sample_rate(self) -> int:
        """Basit: **sabit 48 kHz**. Legend: seçilen hız (44.1/48/88.2/96 kHz dışındaysa 48 kHz)."""
        if self.ui_mode is UiMode.SIMPLE:
            return dev.SIMPLE_RATE
        rate = int(self._get("sample_rate", dev.SIMPLE_RATE) or dev.SIMPLE_RATE)
        return rate if rate in dev.SAMPLE_RATES else dev.SIMPLE_RATE

    def set_sample_rate(self, rate: int) -> None:
        if int(rate) not in dev.SAMPLE_RATES:
            raise ValueError(f"desteklenmeyen örnekleme hızı: {rate}")
        self._set("sample_rate", int(rate))

    @property
    def channels(self) -> int:
        return 2 if (self.ui_mode is UiMode.LEGEND and int(self._get("channels", 1)) == 2) else 1

    def set_channels(self, channels: int) -> None:
        self._set("channels", 2 if int(channels) == 2 else 1)

    @property
    def device_name(self) -> Optional[str]:
        v = self._get("device")
        return str(v) if v else None

    def set_device(self, name: Optional[str]) -> None:
        self._set("device", name or None)

    @property
    def input_gain_db(self) -> float:
        return float(self._get("input_gain_db", 0.0) or 0.0)

    def set_input_gain_db(self, db: float) -> None:
        db = max(-20.0, min(40.0, float(db)))
        self._set("input_gain_db", db)
        self.engine.configure(input_gain_db=db)               # kayıt sürerken canlı uygulanır

    @property
    def gate_enabled(self) -> bool:
        return bool(self._get("gate.enabled", False))        # varsayılan KAPALI (otomasyon kendiliğinden uygulanmaz)

    def set_gate_enabled(self, on: bool) -> None:
        self._set("gate.enabled", bool(on))
        self.engine.configure(gate_enabled=bool(on), gate_params=self.gate_params)

    @property
    def gate_params(self) -> GateParams:
        """Basit: sabit "yumuşak" ön ayar. Legend: kullanıcı değerleri."""
        if self.ui_mode is UiMode.SIMPLE:
            return SIMPLE_GATE
        d = GateParams()
        vals = {k: self._get(f"gate.{k}", getattr(d, k)) for k in ("threshold_db", "attack_ms", "hold_ms",
                                                                    "release_ms", "range_db")}
        try:
            return GateParams(**{k: float(v) for k, v in vals.items()}).clamped()
        except (TypeError, ValueError):
            return d

    def set_gate_param(self, key: str, value: float) -> None:
        if key not in ("threshold_db", "attack_ms", "hold_ms", "release_ms", "range_db"):
            raise KeyError(key)
        self._set(f"gate.{key}", float(value))
        self.engine.configure(gate_params=self.gate_params)

    @property
    def transcribe_language(self) -> str:
        return str(self._get("transcribe.language", "tr") or "tr")

    @property
    def transcribe_model(self) -> str:
        return str(self._get("transcribe.model", "small") or "small")

    def set_transcribe_options(self, language: Optional[str] = None, model: Optional[str] = None) -> None:
        if language is not None:
            self._set("transcribe.language", language)
        if model is not None:
            self._set("transcribe.model", model)

    @property
    def master_target_lufs(self) -> float:
        return float(self._get("master.target_lufs", -14.0))

    def set_master_target(self, lufs: float) -> None:
        self._set("master.target_lufs", max(-30.0, min(-5.0, float(lufs))))

    def save_prompter(self) -> None:
        if self.ctx is not None:
            self.ctx.settings.set("recorder.prompter", self.prompter.to_settings())
        self.settingsChanged.emit()

    # -- aygıtlar ------------------------------------------------------------------------------------------------------------
    def devices(self, refresh: bool = False) -> List[dev.InputDevice]:
        """Giriş aygıtları; backend yoksa/aygıt yoksa ``[]`` (nedeni ``last_error``'da)."""
        if self._devices is None or refresh:
            try:
                self._devices = self._device_lister()
                self.last_error = None
            except SmartError as err:
                self._devices, self.last_error = [], err
            except Exception as exc:
                self._devices, self.last_error = [], dev.map_audio_error(exc)
        return list(self._devices)

    def selected_device(self) -> Optional[dev.InputDevice]:
        devices = self.devices()
        if self.device_name:
            for d in devices:
                if d.name == self.device_name:
                    return d
        return next((d for d in devices if d.is_default), devices[0] if devices else None)

    def availability_error(self) -> Optional[SmartError]:
        """Kayıt yapılamıyorsa nedeni (backend yok / aygıt yok), yapılabiliyorsa ``None``."""
        devices = self.devices()
        if devices:
            return None
        return self.last_error or dev.no_input_error()

    # -- klasör ------------------------------------------------------------------------------------------------------------
    def output_folder(self) -> Path:
        """``02_Audio_WAV`` (açık projenin içinde; proje yoksa proje kökünün altında "Hizli_Kayitlar")."""
        sub = folder_for("wav")
        if self.ctx is not None and self.ctx.project.is_open:
            return self.ctx.project.subfolder(sub)
        root = Path((self._get_global("project_root") or "") or (Path.home() / "KADRAJ_Projeler"))
        return root / "Hizli_Kayitlar" / sub

    def _get_global(self, key: str) -> Any:
        return self.ctx.settings.get(key) if self.ctx is not None else None

    # -- kayıt ----------------------------------------------------------------------------------------------------------------
    def _apply_engine_config(self) -> None:
        device = self.selected_device()
        self.engine.configure(sample_rate=self.sample_rate, channels=self.channels,
                              device=device.index if device else None, input_gain_db=self.input_gain_db,
                              gate_params=self.gate_params, gate_enabled=self.gate_enabled)

    def _check_ready(self) -> None:
        if not self.devices():
            raise (self.last_error or dev.no_input_error())
        if not self._injected_factory:
            bad = dev.check_input(self.engine.device, self.sample_rate, self.channels)
            if bad is not None:
                raise bad

    def _check_disk(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        try:
            free = shutil.disk_usage(str(folder)).free
        except OSError:
            return
        if free < self._min_free:
            raise translate(OSError(errno.ENOSPC, "No space left on device"), {"source": "recorder",
                                                                              "path": str(folder)})

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.stateChanged.emit(state)
        if state in ("recording", "testing") or self.prompter.running:
            self._last_tick = self._clock()
            self._timer.start()
        else:
            self._timer.stop()

    # -- mikrofon izni (Android) ------------------------------------------------------------------------------------------
    @property
    def asking_permission(self) -> bool:
        """İzin penceresi açık (sonuç bekleniyor)."""
        return self._asking_permission

    def _needs_permission(self, then: Callable[[], Any]) -> bool:
        """İzin varsa ``False``. Yoksa sistem penceresini açar, ``True`` döner; izin gelince ``then()`` çağrılır,
        gelmezse telefon ayarlarını anlatan hata kartı (``errorRaised``) gösterilir."""
        if self._mic.status() == mic_permission.GRANTED:
            return False
        if self._asking_permission:
            return True

        def done(granted: bool) -> None:
            self._asking_permission = False
            if not granted:
                err = dev.permission_error()
                self.last_error = err
                self.errorRaised.emit(err)
                return
            try:
                then()
            except Exception:  # noqa: BLE001 — hata kartı errorRaised ile gösterildi
                pass

        self._asking_permission = True
        self._mic.request(self, done)
        return True

    def start_recording(self) -> Optional[Path]:
        """Kaydı başlatır; ``.wav`` yolunu döner. Hazır değilse ``SmartError`` atar (aygıt yok, disk dolu…).

        Android'de mikrofon izni henüz yoksa izin penceresini açıp ``None`` döner; izin gelince kayıt başlar."""
        if self.state == "recording":
            raise RuntimeError("zaten kayıt yapılıyor")
        if self.state == "testing":
            self.cancel_mic_test()
        if self._needs_permission(self.start_recording):
            return None
        try:
            self._apply_engine_config()
            self._check_ready()
            folder = self.output_folder()
            self._check_disk(folder)
            path = new_take_path(folder, self._wall())
            self.engine.start_recording(path)
        except SmartError as err:
            self.last_error = err
            self.errorRaised.emit(err)
            raise
        except Exception as exc:
            err = dev.map_audio_error(exc)
            self.last_error = err
            self.errorRaised.emit(err)
            raise err from exc
        self.last_error = None
        self._rec_started = self._clock()
        if self.prompter.sync and self.prompter.words:
            self.prompter.start()                                  # kayıtla senkron başlat
        self._set_state("recording")
        return path

    def stop_recording(self) -> Optional[Take]:
        """Kaydı bitirir, mikrofonu serbest bırakır, listeyi yeniler ve yeni ``Take``'i döner."""
        if self.state != "recording":
            return None
        try:
            result = self.engine.stop_recording()
        finally:
            self.engine.stop()
        self.prompter.pause()
        self._set_state("idle")
        if result.error is not None:
            self.last_error = result.error
            self.errorRaised.emit(result.error)
        self._register_media(result.path, result.duration_s, result.sample_rate)
        self.refresh_takes()
        take = next((t for t in self._takes if t.path == result.path), None)
        if self.ctx is not None:
            self.ctx.bus.emit(Topics.NOTIFY, {"text": i18n.t("recorder.saved", name=result.path.name,
                                                             seconds=f"{result.duration_s:.0f}"), "level": "info"})
        return take

    def _register_media(self, path: Path, duration: float, rate: int) -> None:
        if self.ctx is None or not self.ctx.project.is_open:
            return
        try:
            self.ctx.project.db.add_media(str(path), "audio", duration=duration, sample_rate=rate, tags="kayit")
        except Exception:
            pass

    # -- mikrofon testi / auto-gain (yalnız ÖNERİ) --------------------------------------------------------------------------
    def start_mic_test(self, seconds: float = MIC_TEST_SECONDS, *, calibration: bool = False) -> None:
        """``seconds`` boyunca dinler (diske yazmaz); bitince ``LevelAnalysis`` önerisi ``micTestFinished`` ile gelir.

        Android'de mikrofon izni henüz yoksa önce izin istenir; izin gelince test başlar."""
        if self.state != "idle":
            raise RuntimeError("meşgul")
        if self._needs_permission(lambda: self.start_mic_test(seconds, calibration=calibration)):
            return
        try:
            self._apply_engine_config()
            self._check_ready()
            self.engine.start_monitoring(collect=True)
        except SmartError as err:
            self.last_error = err
            self.errorRaised.emit(err)
            raise
        except Exception as exc:
            err = dev.map_audio_error(exc)
            self.last_error = err
            self.errorRaised.emit(err)
            raise err from exc
        self.last_test = None
        self._test_deadline = self._clock() + (CALIBRATION_SECONDS if calibration and seconds == MIC_TEST_SECONDS
                                               else seconds)
        self._set_state("testing")

    def cancel_mic_test(self) -> None:
        if self.state == "testing":
            self.engine.stop()
            self._set_state("idle")

    def finish_mic_test(self) -> LevelAnalysis:
        """Testi şimdi bitirip çözümler (süre dolunca otomatik çağrılır)."""
        audio = self.engine.collected()
        rate = self.engine.sample_rate
        gain = self.engine.input_gain_db
        self.engine.stop()
        result = analyze_level(audio, rate, target_dbfs=TARGET_DBFS, current_gain_db=gain)
        self.last_test = result
        self._set_state("idle")
        self.micTestFinished.emit(result)
        return result

    def apply_suggested_gain(self) -> Optional[float]:
        """Son testin önerdiği toplam kazancı uygular (**kullanıcı eylemi**). Öneri yoksa ``None``."""
        if self.last_test is None or self.last_test.verdict == "silent":
            return None
        self.set_input_gain_db(self.last_test.gain_db)
        return self.input_gain_db

    # -- teleprompter -------------------------------------------------------------------------------------------------------------
    def prompter_toggle(self) -> None:
        self.prompter.toggle()
        self._set_state(self.state)                                # zamanlayıcıyı gerekirse başlat/durdur
        self.ticked.emit()

    def prompter_reset(self) -> None:
        self.prompter.reset()
        self._set_state(self.state)
        self.ticked.emit()

    # -- zamanlayıcı (≈30 fps; yalnızca kayıt/test/teleprompter sürerken) ------------------------------------------------------------
    def _tick(self) -> None:
        now = self._clock()
        dt, self._last_tick = now - self._last_tick, now
        self.prompter.tick(dt)
        if self.state == "recording":
            err = self.engine.snapshot().error
            if err is not None:                                    # örn. disk doldu: kaydı düzgünce kapat
                self.last_error = err
                self.stop_recording()                              # hata RecordingResult ile errorRaised'e de gider
                return
        elif self.state == "testing" and now >= self._test_deadline:
            self.finish_mic_test()
        if self.state == "idle" and not self.prompter.running:
            self._timer.stop()
        self.ticked.emit()

    def snapshot(self) -> RecorderSnapshot:
        eng: EngineSnapshot = self.engine.snapshot()
        verdict = "idle"
        if self.state in ("recording", "testing"):
            lv = eng.levels
            if lv.clipping:
                verdict = "clip"
            elif lv.hold_db and max(lv.hold_db) > -3.0:
                verdict = "hot"
            elif lv.max_rms_db > -40.0:
                verdict = "good"
            else:
                verdict = "quiet"
        remaining = max(0.0, self._test_deadline - self._clock()) if self.state == "testing" else 0.0
        return RecorderSnapshot(self.state, eng.elapsed_s, eng.levels, eng.loudness, eng.gate_state, eng.gr_db,
                                eng.xruns, eng.dropped_blocks, verdict, remaining, self.prompter.position,
                                self.prompter.running)

    # -- kayıt listesi ---------------------------------------------------------------------------------------------------------------
    def takes(self) -> List[Take]:
        return list(self._takes)

    def refresh_takes(self) -> List[Take]:
        try:
            self._takes = scan_takes(self.output_folder())
        except OSError:
            self._takes = []
        self.takesChanged.emit()
        return self.takes()

    def _cache_dir(self) -> Optional[Path]:
        if self.ctx is not None and self.ctx.project.is_open:
            return self.ctx.project.root / ".kadraj" / "cache" / "waveforms"
        return None

    def waveform(self, take: Take) -> Optional[List[float]]:
        """Dalga formu özeti; hazır değilse ``None`` döner ve arka planda hesaplar (``waveformReady`` yayınlanır)."""
        key = str(take.path)
        if key in self._peaks:
            return self._peaks[key]
        if key not in self._peaks_pending:
            self._peaks_pending.add(key)
            self._pool.start(_PeaksTask(self, take.path, self._cache_dir()))
        return None

    def _on_peaks(self, path: str, peaks: Any) -> None:
        self._peaks_pending.discard(path)
        self._peaks[path] = list(peaks)
        self.waveformReady.emit(path)

    # -- yazıya çevirme (iş kuyruğu) -------------------------------------------------------------------------------------------------
    def transcribe(self, take: "Take | Path", language: Optional[str] = None, model: Optional[str] = None) -> Job:
        """``.srt`` üretimini **iş kuyruğuna** ekler (kullanıcı tetikler; kendiliğinden çalışmaz)."""
        path = take.path if isinstance(take, Take) else Path(take)
        if path.suffix.lower() != AUDIO_OUTPUT_EXT:
            raise ValueError("yalnızca .wav yazıya çevrilir")
        lang = language or self._get("transcribe.language", "tr")
        size = model or self._get("transcribe.model", "small")
        return self.ctx.jobs.submit(JOB_TRANSCRIBE, {"path": str(path), "language": lang, "model": size},
                                    title=i18n.t("recorder.job.transcribe", name=path.stem))

    def _run_transcribe(self, job: Job, jc: Any) -> Dict[str, Any]:
        p = job.payload
        path = Path(p["path"])
        provider = self.ctx.providers.get("whisper_local") or self.provider
        try:
            transcript = provider.transcribe(path, language=p.get("language"), model_size=p.get("model", "small"),
                                             on_progress=lambda f: jc.report(f * 0.95, i18n.t("recorder.job.writing")),
                                             is_cancelled=jc.is_cancelled)
        except TranscribeCancelled:
            return {}
        out = write_srt(srt_for(path), transcript.segments)
        jc.report(1.0)
        return {"srt": str(out), "segments": len(transcript.segments), "language": transcript.language}

    # -- mastering (iş kuyruğu) ------------------------------------------------------------------------------------------------------------
    def master(self, take: "Take | Path", settings: Optional[MasterSettings] = None) -> Job:
        """Stüdyo mastering'i **iş kuyruğuna** ekler (kullanıcı tetikler). Yeni ``*_master.wav`` üretir, orijinal korunur."""
        path = take.path if isinstance(take, Take) else Path(take)
        if path.suffix.lower() != AUDIO_OUTPUT_EXT:
            raise ValueError("yalnızca .wav mastering'e girer")
        target = (settings.target_lufs if settings else float(self._get("master.target_lufs", -14.0)))
        return self.ctx.jobs.submit(JOB_MASTER, {"path": str(path), "target_lufs": target},
                                    title=i18n.t("recorder.job.master", name=path.stem))

    def _run_master(self, job: Job, jc: Any) -> Dict[str, Any]:
        from dataclasses import replace
        path = Path(job.payload["path"])
        settings = replace(MasterSettings(), target_lufs=float(job.payload.get("target_lufs", -14.0)))
        try:
            res = master_file(path, settings=settings, ffmpeg=find_ffmpeg(), on_progress=jc.report,
                              is_cancelled=jc.is_cancelled)
        except MasterCancelled:
            return {}
        return {"output": str(res.path), "input_lufs": res.measured.get("input_i")}

    def _on_job_updated(self, job: Job) -> None:
        if job.kind not in (JOB_TRANSCRIBE, JOB_MASTER):
            return
        self.jobChanged.emit(job)
        if job.status is JobStatus.DONE:
            self.refresh_takes()
            res = job.payload.get("_result") or {}
            key = "recorder.job.done_srt" if job.kind == JOB_TRANSCRIBE else "recorder.job.done_master"
            name = Path(res.get("srt") or res.get("output") or job.payload.get("path", "")).name
            self.ctx.bus.emit(Topics.NOTIFY, {"text": i18n.t(key, name=name), "level": "info"})
        elif job.status is JobStatus.FAILED and isinstance(job.payload.get("_error"), dict):
            err = smart_error_from_dict(job.payload["_error"])
            self.last_error = err
            self.errorRaised.emit(err)
            self.ctx.bus.emit(Topics.ERROR, err)                  # etkin hata penceresi (eylemli kart)
