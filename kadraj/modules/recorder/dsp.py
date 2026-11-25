"""Ses DSP çekirdeği — yalnızca numpy (scipy yok), Qt yok, sounddevice yok; bu yüzden tamamen sınanabilir.

* ``NoiseGate``     : gerçek zamanlı gürültü kapısı (eşik / atak / tutma / bırakma / aralık + histerezis). Ses geri çağrısında
  çalışır: blok 2 ms'lik alt bloklara bölünür, kazanç alt blok başına bir kez hesaplanıp doğrusal rampayla uygulanır
  (tıkırtı yok, Python döngüsü blok başına ~10 yineleme).
* ``LevelMeter``    : kanal başına tepe / RMS / tepe tutma (düşüşlü) / clipping mandalı. Süreyi **blok uzunluğundan** sayar
  (duvar saati yok) → deterministik.
* ``LoudnessMeter`` : ITU-R BS.1770-4 K-ağırlıklı LUFS-M (400 ms), LUFS-S (3 s), kapılı entegre değer ve 4× fazla örneklemeli
  true peak. K filtresi, çift ikinci-derece (biquad) kademenin impuls yanıtından türetilen FIR ile FFT üzerinden
  uygulanır (numpy-only, akışlı).
* ``analyze_level`` : mikrofon testi / auto-gain kalibrasyonu (-18 dBFS hedef): konuşma seviyesi, gürültü tabanı, tepe,
  önerilen kazanç ve hüküm (sessiz / kısık / iyi / yüksek / clipping).

Hiçbir şey kendiliğinden uygulanmaz: ``analyze_level`` yalnızca **öneri** döner; kazancı uygulamak kullanıcının eylemidir.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import numpy as np

FLOOR_DB = -120.0
CLIP_THRESHOLD = 0.999            # ≈ -0.0087 dBFS: tam ölçek sayılır
TARGET_DBFS = -18.0               # auto-gain hedefi


def lin_to_db(x: float) -> float:
    return FLOOR_DB if x <= 1e-6 else 20.0 * math.log10(x)


def db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


def power_to_db(p: float) -> float:
    return FLOOR_DB if p <= 1e-12 else 10.0 * math.log10(p)


def as_2d(block: np.ndarray) -> np.ndarray:
    """(frames,) → (frames, 1); (frames, ch) olduğu gibi. float32'ye çevirmez (çağıran karar verir)."""
    return block[:, None] if block.ndim == 1 else block


# ---------------------------------------------------------------------------
# Gürültü kapısı
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GateParams:
    threshold_db: float = -45.0      # bu seviyenin üstündeki ses kapıyı açar
    attack_ms: float = 5.0           # açılma süresi
    hold_ms: float = 120.0           # seviye düştükten sonra açık kalma
    release_ms: float = 150.0        # kapanma süresi
    range_db: float = -60.0          # kapalıyken uygulanan azaltma ("aralık"); -60 ≈ neredeyse sessiz
    hysteresis_db: float = 3.0       # kapanma eşiği = eşik - histerezis (titremeyi önler)

    def clamped(self) -> "GateParams":
        return GateParams(
            threshold_db=min(0.0, max(-90.0, float(self.threshold_db))),
            attack_ms=min(100.0, max(0.1, float(self.attack_ms))),
            hold_ms=min(2000.0, max(0.0, float(self.hold_ms))),
            release_ms=min(2000.0, max(1.0, float(self.release_ms))),
            range_db=min(0.0, max(-90.0, float(self.range_db))),
            hysteresis_db=min(12.0, max(0.0, float(self.hysteresis_db))))


#: Basit Mod'un "Arka plandaki vızıltıyı sustur" ön ayarı (yumuşak, konuşma dostu).
SIMPLE_GATE = GateParams(threshold_db=-42.0, attack_ms=5.0, hold_ms=180.0, release_ms=220.0, range_db=-40.0,
                         hysteresis_db=3.0)


class NoiseGate:
    """Gerçek zamanlı kapı. ``process`` bloğu işler ve **yeni** bir dizi döner (girdiyi değiştirmez)."""

    SUB_MS = 2.0
    STATES = ("bypass", "closed", "open", "hold")

    def __init__(self, sample_rate: int, params: Optional[GateParams] = None, enabled: bool = False) -> None:
        self.sr = int(sample_rate)
        self._params = (params or GateParams()).clamped()
        self.enabled = bool(enabled)
        self.state = "bypass" if not enabled else "closed"
        self._gain = 1.0
        self._hold_left = 0.0
        self.gr_db = 0.0

    @property
    def params(self) -> GateParams:
        return self._params

    def set_params(self, params: GateParams) -> None:
        self._params = params.clamped()               # atomik referans değişimi: callback güvenle okur

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.state, self._gain, self.gr_db, self._hold_left = "bypass", 1.0, 0.0, 0.0
        elif self.state == "bypass":
            self.state = "closed"

    def process(self, block: np.ndarray) -> np.ndarray:
        x = as_2d(block)
        if not self.enabled:
            self.state, self.gr_db = "bypass", 0.0
            return np.array(block, copy=True)
        p = self._params
        floor_gain = db_to_lin(p.range_db)
        open_thr = db_to_lin(p.threshold_db)
        close_thr = db_to_lin(p.threshold_db - p.hysteresis_db)
        n = max(16, int(self.sr * self.SUB_MS / 1000.0))
        out = np.empty_like(x)
        gain = self._gain
        for start in range(0, len(x), n):
            sub = x[start:start + n]
            dt = len(sub) / self.sr * 1000.0
            level = float(np.max(np.abs(sub))) if len(sub) else 0.0
            if level >= open_thr or (self.state in ("open", "hold") and level >= close_thr):
                self.state, self._hold_left = "open", p.hold_ms
            elif self.state in ("open", "hold"):
                self._hold_left -= dt
                self.state = "hold" if self._hold_left > 0 else "closed"
            target = 1.0 if self.state in ("open", "hold") else floor_gain
            tau = p.attack_ms if target > gain else p.release_ms
            alpha = 1.0 - math.exp(-dt / max(tau, 0.05))
            new_gain = gain + alpha * (target - gain)
            ramp = np.linspace(gain, new_gain, len(sub), endpoint=False, dtype=np.float32)
            out[start:start + len(sub)] = sub * ramp[:, None]
            gain = new_gain
        self._gain = gain
        self.gr_db = max(0.0, -lin_to_db(gain)) if gain < 0.999 else 0.0
        return out.reshape(block.shape)


# ---------------------------------------------------------------------------
# Seviye ölçer (VU)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LevelSnapshot:
    peak_db: Tuple[float, ...] = ()           # son bloktaki tepe (dBFS), kanal başına
    rms_db: Tuple[float, ...] = ()            # son blok RMS (dBFS)
    hold_db: Tuple[float, ...] = ()           # tepe tutma (dBFS)
    clipping: bool = False                    # son ``clip_hold_s`` içinde clipping oldu mu (mandal)
    clip_samples: int = 0                     # toplam clipping örnek sayısı

    @property
    def max_peak_db(self) -> float:
        return max(self.peak_db) if self.peak_db else FLOOR_DB

    @property
    def max_rms_db(self) -> float:
        return max(self.rms_db) if self.rms_db else FLOOR_DB


class LevelMeter:
    def __init__(self, sample_rate: int, channels: int, *, hold_s: float = 1.5, fall_db_per_s: float = 20.0,
                 clip_hold_s: float = 2.0) -> None:
        self.sr, self.ch = int(sample_rate), int(channels)
        self.hold_s, self.fall, self.clip_hold_s = hold_s, fall_db_per_s, clip_hold_s
        self.reset()

    def reset(self) -> None:
        self._hold = [FLOOR_DB] * self.ch
        self._hold_t = [0.0] * self.ch
        self._clip_t = 0.0
        self._clip_samples = 0
        self._snap = LevelSnapshot((FLOOR_DB,) * self.ch, (FLOOR_DB,) * self.ch, (FLOOR_DB,) * self.ch)

    def process(self, block: np.ndarray) -> LevelSnapshot:
        x = as_2d(block)
        dt = len(x) / self.sr
        peaks, rmss = [], []
        clipped = 0
        for c in range(min(self.ch, x.shape[1])):
            col = x[:, c]
            peak = float(np.max(np.abs(col))) if len(col) else 0.0
            rms = float(np.sqrt(np.mean(col.astype(np.float64) ** 2))) if len(col) else 0.0
            clipped += int(np.count_nonzero(np.abs(col) >= CLIP_THRESHOLD))
            pk_db = lin_to_db(peak)
            peaks.append(pk_db)
            rmss.append(lin_to_db(rms))
            if pk_db >= self._hold[c]:
                self._hold[c], self._hold_t[c] = pk_db, 0.0
            else:
                self._hold_t[c] += dt
                if self._hold_t[c] > self.hold_s:
                    self._hold[c] = max(pk_db, self._hold[c] - self.fall * dt)
        while len(peaks) < self.ch:                                 # mono kaynak → eksik kanalı çoğalt
            peaks.append(peaks[-1] if peaks else FLOOR_DB)
            rmss.append(rmss[-1] if rmss else FLOOR_DB)
        if clipped:
            self._clip_t, self._clip_samples = self.clip_hold_s, self._clip_samples + clipped
        else:
            self._clip_t = max(0.0, self._clip_t - dt)
        self._snap = LevelSnapshot(tuple(peaks), tuple(rmss), tuple(self._hold[:self.ch]), self._clip_t > 0,
                                   self._clip_samples)
        return self._snap

    @property
    def snapshot(self) -> LevelSnapshot:
        return self._snap


# ---------------------------------------------------------------------------
# Yüksek sesliliği (BS.1770-4)
# ---------------------------------------------------------------------------
def _k_biquads(fs: float) -> List[Tuple[np.ndarray, np.ndarray]]:
    """BS.1770-4 K ağırlıklandırması: 1) yüksek raf (≈ +4 dB, 1.68 kHz) 2) RLB yüksek geçiren (38 Hz)."""
    G, Q, fc = 3.999843853973347, 0.7071752369554196, 1681.974450955533
    K = math.tan(math.pi * fc / fs)
    Vh = 10.0 ** (G / 20.0)
    Vb = Vh ** 0.4996667741545416
    a0 = 1.0 + K / Q + K * K
    b1 = np.array([(Vh + Vb * K / Q + K * K) / a0, 2.0 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0])
    a1 = np.array([1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0])
    Q2, fc2 = 0.5003270373238773, 38.13547087613982
    K2 = math.tan(math.pi * fc2 / fs)
    a0b = 1.0 + K2 / Q2 + K2 * K2
    b2 = np.array([1.0, -2.0, 1.0])
    a2 = np.array([1.0, 2.0 * (K2 * K2 - 1.0) / a0b, (1.0 - K2 / Q2 + K2 * K2) / a0b])
    return [(b1, a1), (b2, a2)]


@lru_cache(maxsize=8)
def _k_fir(sample_rate: int) -> np.ndarray:
    """K filtresinin impuls yanıtı (≈ 100 ms; kutup zaman sabiti ~4 ms → kesme hatası ihmal edilir)."""
    n = max(512, int(sample_rate * 0.1))
    x = np.zeros(n)
    x[0] = 1.0
    for b, a in _k_biquads(float(sample_rate)):
        y = np.zeros(n)
        z1 = z2 = 0.0
        for i in range(n):                                          # bir kerelik, önbelleğe alınır
            xi = x[i]
            yi = b[0] * xi + z1
            z1 = b[1] * xi - a[1] * yi + z2
            z2 = b[2] * xi - a[2] * yi
            y[i] = yi
        x = y
    return x


class _FirStream:
    """FFT tabanlı akışlı FIR (overlap-add). Çıktı, girdiyle aynı uzunluktadır."""

    def __init__(self, h: np.ndarray) -> None:
        self.h = np.asarray(h, dtype=np.float64)
        self.tail = np.zeros(len(h) - 1)
        self._cache: Dict[int, np.ndarray] = {}

    def process(self, x: np.ndarray) -> np.ndarray:
        n, m = len(x), len(self.h)
        size = 1 << int(math.ceil(math.log2(n + m - 1)))
        if size not in self._cache:
            self._cache[size] = np.fft.rfft(self.h, size)
        y = np.fft.irfft(np.fft.rfft(x, size) * self._cache[size], size)[:n + m - 1]
        y[:len(self.tail)] += self.tail
        self.tail = y[n:].copy()
        return y[:n]


@lru_cache(maxsize=4)
def _tp_kernel() -> np.ndarray:
    """4× fazla örnekleme için pencereli sinc (49 tap, kazanç 4)."""
    taps = 49
    t = (np.arange(taps) - (taps - 1) / 2.0) / 4.0
    h = np.sinc(t) * np.hamming(taps)
    return h / np.sum(h) * 4.0


@dataclass(frozen=True)
class LoudnessSnapshot:
    momentary: float = FLOOR_DB        # LUFS-M (400 ms)
    short_term: float = FLOOR_DB       # LUFS-S (3 s)
    integrated: float = FLOOR_DB       # kapılı entegre LUFS-I
    true_peak_db: float = FLOOR_DB     # dBTP (4×)


class LoudnessMeter:
    STEP_S = 0.1
    _TP_PAD = 6          # 49 tap'lık çekirdeğin 4× çıktıdaki yarı genişliği (24) / 4 = 6 örnek

    def __init__(self, sample_rate: int, channels: int) -> None:
        self.sr, self.ch = int(sample_rate), int(channels)
        self.reset()

    def reset(self) -> None:
        h = _k_fir(self.sr)
        self._fir = [_FirStream(h) for _ in range(self.ch)]
        self._seg_len = int(self.sr * self.STEP_S)
        self._acc = 0.0
        self._acc_n = 0
        self._segs: List[float] = []              # 100 ms'lik ortalama kareler (kanallar toplanmış)
        self._windows: List[float] = []           # 400 ms'lik kayan pencereler (z)
        self._tp = FLOOR_DB
        self._tp_hist = [np.zeros(2 * self._TP_PAD) for _ in range(self.ch)]
        self._snap = LoudnessSnapshot()

    def process(self, block: np.ndarray) -> LoudnessSnapshot:
        x = as_2d(block).astype(np.float64)
        ch = min(self.ch, x.shape[1])
        weighted = np.stack([self._fir[c].process(x[:, c]) for c in range(ch)], axis=1)
        sq = np.sum(weighted ** 2, axis=1)         # kanal ağırlığı 1.0 (L/R/mono)
        self._update_true_peak(x[:, :ch])
        pos = 0
        while pos < len(sq):
            take = min(self._seg_len - self._acc_n, len(sq) - pos)
            self._acc += float(np.sum(sq[pos:pos + take]))
            self._acc_n += take
            pos += take
            if self._acc_n >= self._seg_len:
                self._segs.append(self._acc / self._acc_n)
                self._acc, self._acc_n = 0.0, 0
                if len(self._segs) >= 4:
                    self._windows.append(sum(self._segs[-4:]) / 4.0)
        self._snap = self._compute()
        return self._snap

    def _update_true_peak(self, x: np.ndarray) -> None:
        """4× fazla örnekleme. Blok kenarlarında kesik-çekirdek taşması olmasın diye her örnek **tam bağlamla**
        (önce/sonra 6'şar örnek) bir kez değerlendirilir; son 6 örnek bir sonraki bloğa kalır (≈ 0.1 ms gecikme)."""
        k = _tp_kernel()
        pad = self._TP_PAD
        for c in range(x.shape[1]):
            ext = np.concatenate([self._tp_hist[c], x[:, c]])
            if len(ext) <= 2 * pad:
                self._tp_hist[c] = ext
                continue
            up = np.zeros(len(ext) * 4)
            up[::4] = ext
            y = np.convolve(up, k, mode="same")[pad * 4:(len(ext) - pad) * 4]
            if len(y):
                self._tp = max(self._tp, lin_to_db(float(np.max(np.abs(y)))))
            self._tp_hist[c] = ext[-2 * pad:]

    def _compute(self) -> LoudnessSnapshot:
        mom = -0.691 + power_to_db(sum(self._segs[-4:]) / 4.0) if len(self._segs) >= 4 else FLOOR_DB
        st = -0.691 + power_to_db(sum(self._segs[-30:]) / len(self._segs[-30:])) if len(self._segs) >= 30 else \
            (mom if len(self._segs) >= 4 else FLOOR_DB)
        return LoudnessSnapshot(mom, st, self._integrated(), self._tp)

    def _integrated(self) -> float:
        if not self._windows:
            return FLOOR_DB
        z = np.asarray(self._windows)
        loud = -0.691 + 10.0 * np.log10(np.maximum(z, 1e-12))
        above = z[loud > -70.0]
        if len(above) == 0:
            return FLOOR_DB
        rel = -0.691 + power_to_db(float(np.mean(above))) - 10.0
        kept = z[loud > rel]
        if len(kept) == 0:
            return FLOOR_DB
        return -0.691 + power_to_db(float(np.mean(kept)))

    @property
    def snapshot(self) -> LoudnessSnapshot:
        return self._snap


def measure_loudness(samples: np.ndarray, sample_rate: int, chunk: int = 48000) -> LoudnessSnapshot:
    """Bir dizinin (kanallı olabilir) entegre LUFS ve true peak değerini ölçer (çevrimdışı)."""
    x = as_2d(np.asarray(samples))
    meter = LoudnessMeter(sample_rate, x.shape[1])
    snap = meter.snapshot
    for i in range(0, len(x), chunk):
        snap = meter.process(x[i:i + chunk])
    return snap


# ---------------------------------------------------------------------------
# Mikrofon testi / auto-gain
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LevelAnalysis:
    verdict: str                       # silent | quiet | good | loud | clipping
    level_dbfs: float                  # konuşma seviyesi (konuşma çerçevelerinin ortalama gücü)
    noise_floor_dbfs: float
    peak_dbfs: float
    gain_db: float                     # önerilen TOPLAM giriş kazancı (mevcut + düzeltme)
    delta_db: float                    # mevcut kazanca eklenecek düzeltme
    limited_by_peak: bool = False
    target_dbfs: float = TARGET_DBFS
    speech_ratio: float = 0.0          # konuşma çerçevelerinin oranı


def analyze_level(samples: np.ndarray, sample_rate: int, *, target_dbfs: float = TARGET_DBFS,
                  current_gain_db: float = 0.0, max_gain_db: float = 36.0, min_gain_db: float = -20.0,
                  peak_ceiling_db: float = -3.0) -> LevelAnalysis:
    """Test kaydını çözümler. ``samples`` mevcut ``current_gain_db`` uygulanmış sesi içerir; dönen ``gain_db`` toplam kazançtır."""
    x = as_2d(np.asarray(samples, dtype=np.float64))
    mono_power = np.mean(x ** 2, axis=1) if x.size else np.zeros(0)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    peak_db = lin_to_db(peak)
    if peak_db < -70.0 or len(mono_power) < sample_rate // 20:
        return LevelAnalysis("silent", FLOOR_DB, FLOOR_DB, peak_db, current_gain_db, 0.0, False, target_dbfs, 0.0)
    frame = max(1, int(sample_rate * 0.05))
    n_frames = len(mono_power) // frame
    frames = mono_power[:n_frames * frame].reshape(n_frames, frame).mean(axis=1)
    frame_db = 10.0 * np.log10(np.maximum(frames, 1e-12))
    noise_floor = float(np.percentile(frame_db, 10))
    speech = frames[(frame_db > noise_floor + 8.0) & (frame_db > -60.0)]
    if len(speech) < max(3, int(0.05 * n_frames)):
        speech = frames[frame_db > -60.0]
    level_db = power_to_db(float(np.mean(speech))) if len(speech) else power_to_db(float(np.mean(frames)))
    ratio = float(len(speech)) / max(1, n_frames)
    clipped = bool(np.any(np.abs(x) >= CLIP_THRESHOLD))
    delta = target_dbfs - level_db
    total = min(max_gain_db, max(min_gain_db, current_gain_db + delta))
    limited = False
    if peak_db + (total - current_gain_db) > peak_ceiling_db:        # tepe tavanı: kırpılmayı önle
        total = min(total, current_gain_db + (peak_ceiling_db - peak_db))
        limited = True
    delta = total - current_gain_db
    if clipped:
        verdict = "clipping"
    elif level_db < -30.0:
        verdict = "quiet"
    elif level_db > -12.0:
        verdict = "loud"
    else:
        verdict = "good"
    return LevelAnalysis(verdict, level_db, noise_floor, peak_db, total, delta, limited, target_dbfs, ratio)
