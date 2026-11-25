"""Stüdyo mastering zinciri — FFmpeg filtreleriyle, **tamamen çevrimdışı**.

Zincir (konuşma için): yüksek geçiren (80 Hz) → EQ (çamurluk kırp, sunuş/hava ekle) → kompresör → de-esser →
``loudnorm`` (iki geçiş, doğrusal; hedef **-14 LUFS**, true peak -1 dBTP) → limiter.

Kurallar (plan §0.3): **asla kendiliğinden uygulanmaz** — yalnızca kullanıcı tetikler. **Yeni dosya** üretir
(``<ad>_master.wav``, var olanın üzerine yazmaz); **orijinal dosyaya hiç dokunulmaz**. Çıktı yalnızca ``.wav`` (PCM 24-bit,
aynı örnekleme hızı). İşlem iptal edilebilir (FFmpeg süreci sonlandırılır, yarım dosya silinir).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...core.contracts import AUDIO_OUTPUT_EXT, SmartError
from ...core.errors import translate

MASTER_SUFFIX = "_master"


@dataclass(frozen=True)
class MasterSettings:
    target_lufs: float = -14.0
    true_peak_db: float = -1.0
    lra: float = 11.0
    highpass_hz: float = 80.0
    # EQ: (tür, frekans, q/genişlik, kazanç dB) — tür: "peak" | "highshelf"
    eq: Tuple[Tuple[str, float, float, float], ...] = (("peak", 200.0, 1.0, -2.0), ("peak", 3000.0, 1.2, 2.0),
                                                       ("highshelf", 9000.0, 0.7, 1.5))
    comp_threshold_db: float = -18.0
    comp_ratio: float = 3.0
    comp_attack_ms: float = 15.0
    comp_release_ms: float = 180.0
    deess_intensity: float = 0.4          # 0..1
    deess_max: float = 0.5                # azaltma üst sınırı 0..1
    deess_freq: float = 0.5               # 0..1 (≈ yüksek frekans bandı)
    limiter_ceiling_db: float = -1.0

    def clamped(self) -> "MasterSettings":
        from dataclasses import replace
        return replace(self, target_lufs=min(-5.0, max(-30.0, self.target_lufs)),
                       true_peak_db=min(-0.1, max(-9.0, self.true_peak_db)),
                       lra=min(20.0, max(1.0, self.lra)),
                       comp_ratio=min(20.0, max(1.0, self.comp_ratio)),
                       deess_intensity=min(1.0, max(0.0, self.deess_intensity)),
                       limiter_ceiling_db=min(-0.1, max(-9.0, self.limiter_ceiling_db)))


def _num(x: float) -> str:
    return f"{x:.4g}"


def db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


def build_chain(s: MasterSettings) -> str:
    """``loudnorm`` ve limiter **dışındaki** kısım: HP → EQ → kompresör → de-esser."""
    s = s.clamped()
    parts: List[str] = [f"highpass=f={_num(s.highpass_hz)}"]
    for kind, freq, q, gain in s.eq:
        if kind == "highshelf":
            parts.append(f"highshelf=f={_num(freq)}:g={_num(gain)}:w={_num(q)}")
        else:
            parts.append(f"equalizer=f={_num(freq)}:t=q:w={_num(q)}:g={_num(gain)}")
    parts.append(f"acompressor=threshold={_num(db_to_linear(s.comp_threshold_db))}:ratio={_num(s.comp_ratio)}"
                 f":attack={_num(s.comp_attack_ms)}:release={_num(s.comp_release_ms)}:makeup=1")
    parts.append(f"deesser=i={_num(s.deess_intensity)}:m={_num(s.deess_max)}:f={_num(s.deess_freq)}")
    return ",".join(parts)


def build_loudnorm(s: MasterSettings, measured: Optional[Dict[str, Any]] = None) -> str:
    s = s.clamped()
    base = f"loudnorm=I={_num(s.target_lufs)}:TP={_num(s.true_peak_db)}:LRA={_num(s.lra)}"
    if measured is None:
        return base + ":print_format=json"
    return (base + f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
                   f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
                   f":offset={measured['target_offset']}:linear=true:print_format=summary")


def build_limiter(s: MasterSettings) -> str:
    return f"alimiter=limit={_num(db_to_linear(s.clamped().limiter_ceiling_db))}:level=disabled"


def build_filter(s: MasterSettings, measured: Optional[Dict[str, Any]] = None, final: bool = True) -> str:
    """Tam ``-af`` zinciri. ``final=False`` ölçüm geçişi (limiter yok)."""
    parts = [build_chain(s), build_loudnorm(s, measured)]
    if final:
        parts.append(build_limiter(s))
    return ",".join(parts)


def find_ffmpeg() -> Optional[str]:
    from ...core import components      # tembel: ağır bağımlılık yok, yalnızca dosya araması
    return components.ffmpeg_path()


_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def parse_loudnorm_json(stderr: str) -> Dict[str, Any]:
    m = _JSON.findall(stderr or "")
    if not m:
        raise ValueError("loudnorm ölçümü okunamadı")
    data = json.loads(m[-1])
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        if key not in data or str(data[key]).lower() in ("-inf", "inf", "nan"):
            raise ValueError("loudnorm ölçümü geçersiz (sessiz dosya?)")
    return data


def output_path(src: Path, suffix: str = MASTER_SUFFIX) -> Path:
    """``<ad>_master.wav``; varsa ``_master2``, ``_master3``… (asla üzerine yazmaz)."""
    src = Path(src)
    cand = src.with_name(f"{src.stem}{suffix}{AUDIO_OUTPUT_EXT}")
    n = 2
    while cand.exists():
        cand = src.with_name(f"{src.stem}{suffix}{n}{AUDIO_OUTPUT_EXT}")
        n += 1
    return cand


@dataclass(frozen=True)
class MasterResult:
    path: Path
    measured: Dict[str, Any] = field(default_factory=dict)       # birinci geçişin ölçümü (önceki durum)
    duration_s: float = 0.0


class MasterCancelled(Exception):
    pass


def _run(cmd: List[str], duration: float, lo: float, hi: float, on_progress: Optional[Callable[[float], None]],
         is_cancelled: Optional[Callable[[], bool]], popen: Callable[..., Any]) -> Tuple[int, str]:
    """FFmpeg'i çalıştırır; ``-progress`` çıktısından ilerlemeyi ``[lo, hi]`` aralığına eşler. (dönüş kodu, stderr)."""
    err = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=err, stdin=subprocess.DEVNULL, text=True)
    try:
        try:
            for line in proc.stdout:                                 # type: ignore[union-attr]
                if is_cancelled is not None and is_cancelled():
                    proc.terminate()
                    proc.wait(timeout=10)
                    raise MasterCancelled()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        secs = int(line.split("=", 1)[1]) / 1_000_000.0
                    except ValueError:
                        continue
                    if on_progress is not None and duration > 0:
                        on_progress(lo + (hi - lo) * min(1.0, secs / duration))
            rc = proc.wait()
        except BaseException:
            if proc.poll() is None:
                proc.kill()
            raise
        err.seek(0)
        return rc, err.read()
    finally:
        out = getattr(proc, "stdout", None)
        if hasattr(out, "close"):
            out.close()                                              # borular/geçici dosya iptalde de kapanır
        err.close()


def master_file(src: Path, *, settings: Optional[MasterSettings] = None, ffmpeg: Optional[str] = None,
                on_progress: Optional[Callable[[float], None]] = None,
                is_cancelled: Optional[Callable[[], bool]] = None,
                popen: Callable[..., Any] = subprocess.Popen) -> MasterResult:
    """``src``'yi mastering zincirinden geçirip **yeni** bir ``.wav`` üretir. Orijinal korunur.

    Hatalar ``SmartError`` olarak atılır (FFmpeg yok → "FFmpeg bulunamadı"; ffmpeg hatası → Haiku'nun hata sözlüğü).
    İptal → ``MasterCancelled`` (yarım çıktı silinir).
    """
    import soundfile as sf
    src = Path(src)
    s = (settings or MasterSettings()).clamped()
    exe = ffmpeg or find_ffmpeg()
    if exe is None:
        raise translate(FileNotFoundError("ffmpeg"), {"tool": "ffmpeg", "source": "recorder"})
    if not src.is_file():
        raise translate(FileNotFoundError(2, "No such file", str(src)), {"source": "recorder", "path": str(src)})
    try:
        info = sf.info(str(src))
    except Exception as exc:                                         # bozuk/yanlış biçimli dosya
        raise translate(exc, {"tool": "ffmpeg", "source": "recorder", "path": str(src)}) from exc
    duration, rate = float(info.duration), int(info.samplerate)
    dst = output_path(src)
    tmp = dst.with_name(f".{dst.stem}.tmp{AUDIO_OUTPUT_EXT}")
    base = [exe, "-hide_banner", "-nostdin", "-nostats", "-progress", "pipe:1", "-i", str(src)]
    try:
        # 1. geçiş: ölçüm
        rc, text = _run(base + ["-af", build_filter(s, None, final=False), "-f", "null", "-"], duration, 0.0, 0.4,
                        on_progress, is_cancelled, popen)
        if rc != 0:
            raise _ffmpeg_error(rc, text)
        try:
            measured = parse_loudnorm_json(text)
        except ValueError as exc:
            raise _ffmpeg_error(rc, f"{exc}\n{text}", code="silent") from exc
        # 2. geçiş: uygula
        rc, text = _run(base + ["-y", "-af", build_filter(s, measured, final=True), "-ar", str(rate), "-c:a",
                                "pcm_s24le", "-f", "wav", str(tmp)], duration, 0.4, 1.0, on_progress, is_cancelled,
                        popen)
        if rc != 0:
            raise _ffmpeg_error(rc, text)
        os.replace(tmp, dst)
        if on_progress is not None:
            on_progress(1.0)
        return MasterResult(dst, measured, duration)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _ffmpeg_error(rc: int, stderr: str, code: str = "") -> SmartError:
    tail = (stderr or "")[-2000:]
    exc = RuntimeError(f"ffmpeg exited with {rc}")
    return translate(exc, {"tool": "ffmpeg", "stderr": tail, "returncode": rc, "source": "recorder", "reason": code})
