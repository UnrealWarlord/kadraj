"""FFmpeg işleme motoru: şablonlar, kalite/boyut/süre tahmini, donanım kodlayıcı algılama (NVENC → VAAPI → CPU),
komut kurma (1–2 geçiş, CBR/VBR) ve ilerlemeli çalıştırma. Çıktı **geçici ``.part``** dosyasına yazılır, başarıda adı konur.
"""
from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .errors import smart
from .filtergraph import BuiltGraph, InfoFn, build_graph, fps_arg, window
from .probe import find_binary, require_ffmpeg
from .timeline import Timeline

CODECS = ("h264", "h265", "prores", "wav")
QUALITIES = ("fast", "balanced", "heavy")
BACKENDS = ("nvenc", "vaapi", "cpu")
VAAPI_DEVICE = "/dev/dri/renderD128"


# ---------------------------------------------------------------------------
# Ayarlar + şablonlar
# ---------------------------------------------------------------------------
@dataclass
class RenderSettings:
    codec: str = "h264"                   # h264 | h265 | prores | wav (yalnızca ses)
    quality: str = "balanced"             # fast | balanced | heavy  (Hızlı / Dengeli / Ağır)
    rate_mode: str = "vbr"                # cbr | vbr
    passes: int = 1                       # 1 | 2
    bitrate_kbps: int = 0                 # 0 → kalite modelinden (VBR 1 geçişte CRF)
    fps: float = 0.0                      # 0 → zaman çizelgesinin fps'i
    width: int = 0                        # 0 → zaman çizelgesinin boyutu
    height: int = 0
    audio_kbps: int = 192
    lufs: Optional[float] = -14.0         # None → ses normalize edilmez
    hw: str = "auto"                      # auto | cpu | nvenc | vaapi
    max_size_mb: float = 0.0              # >0 → bit hızı süreye göre sınırlanır (WhatsApp)
    max_seconds: float = 0.0              # >0 → şablon süre sınırı (yalnızca uyarı)
    template: str = ""
    t_start: Optional[float] = None       # aralık dışa aktarma (sn)
    t_end: Optional[float] = None
    chapters: bool = True                 # bölüm işaretçilerini dosya meta verisine yaz

    @property
    def container(self) -> str:
        return {"prores": "mov", "wav": "wav"}.get(self.codec, "mp4")

    @property
    def ext(self) -> str:
        return "." + self.container

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "RenderSettings":
        names = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in (d or {}).items() if k in names})
        if s.codec not in CODECS:
            s.codec = "h264"
        if s.quality not in QUALITIES:
            s.quality = "balanced"
        s.passes = 2 if int(s.passes) == 2 else 1
        s.rate_mode = "cbr" if s.rate_mode == "cbr" else "vbr"
        return s


@dataclass(frozen=True)
class Template:
    id: str
    settings: RenderSettings
    aspect: str = "16:9"
    note_key: str = ""


def _tpl(tid: str, aspect: str, note: str = "", **kw: Any) -> Template:
    return Template(tid, RenderSettings(template=tid, **kw), aspect, note)


TEMPLATES: Dict[str, Template] = {t.id: t for t in (
    _tpl("youtube", "16:9", width=1920, height=1080, fps=30, codec="h264", quality="balanced", lufs=-14.0, audio_kbps=192),
    _tpl("shorts", "9:16", width=1080, height=1920, fps=30, codec="h264", quality="balanced", lufs=-14.0, max_seconds=180),
    _tpl("reels", "9:16", width=1080, height=1920, fps=30, codec="h264", quality="balanced", lufs=-14.0, max_seconds=90),
    _tpl("tiktok", "9:16", width=1080, height=1920, fps=30, codec="h264", quality="balanced", lufs=-14.0, max_seconds=600),
    _tpl("podcast", "audio", codec="wav", lufs=-16.0, audio_kbps=0),
    _tpl("whatsapp", "16:9", width=1280, height=720, fps=30, codec="h264", quality="fast", lufs=-16.0, audio_kbps=96,
         max_size_mb=15.5),
    _tpl("archive", "16:9", codec="prores", quality="heavy", lufs=None, audio_kbps=0),
)}
TEMPLATE_ORDER = tuple(TEMPLATES)


def template_settings(tid: str) -> RenderSettings:
    return copy.deepcopy(TEMPLATES[tid].settings)


# ---------------------------------------------------------------------------
# Kalite / boyut / süre tahmini
# ---------------------------------------------------------------------------
_BASE_KBPS = {"h264": {"fast": 9000, "balanced": 7500, "heavy": 6000}, "h265": {"fast": 5400, "balanced": 4500, "heavy": 3600},
              "prores": {"fast": 147000, "balanced": 147000, "heavy": 220000}}
_REALTIME = {"cpu": {"fast": 0.22, "balanced": 0.55, "heavy": 1.5}, "nvenc": {"fast": 0.08, "balanced": 0.12, "heavy": 0.22},
             "vaapi": {"fast": 0.1, "balanced": 0.15, "heavy": 0.25}}
_CODEC_COST = {"h264": 1.0, "h265": 2.2, "prores": 0.45, "wav": 0.0}


@dataclass(frozen=True)
class Estimate:
    size_bytes: int
    seconds: float
    video_kbps: int


def model_kbps(s: RenderSettings, width: int, height: int, fps: float) -> int:
    base = _BASE_KBPS.get(s.codec, {}).get(s.quality, 0)
    pix = max(1.0, width * height / (1920 * 1080))
    kbps = base * pix * (max(1.0, fps) / 30.0) ** 0.6
    return int(round(kbps))


def effective_video_kbps(s: RenderSettings, duration: float, width: int, height: int, fps: float) -> int:
    if s.codec == "wav":
        return 0
    kbps = s.bitrate_kbps or model_kbps(s, width, height, fps)
    if s.max_size_mb > 0 and duration > 0:
        cap = int(s.max_size_mb * 8 * 1024 * 0.96 / duration - s.audio_kbps)
        kbps = max(150, min(kbps, cap))
    return kbps


def estimate(s: RenderSettings, duration: float, width: int, height: int, fps: float, backend: str = "cpu") -> Estimate:
    """Tahmini dosya boyutu (bayt) ve işleme süresi (sn). Gerçek değer içeriğe göre ±%40 sapabilir."""
    duration = max(0.0, duration)
    if s.codec == "wav":
        return Estimate(int(duration * 48000 * 2 * 3), duration * 0.03 + 0.5, 0)
    kbps = effective_video_kbps(s, duration, width, height, fps)
    audio = s.audio_kbps if s.codec != "prores" else 1152
    size = int((kbps + audio) * 1000 / 8 * duration)
    pix = max(0.25, width * height / (1920 * 1080))
    factor = _REALTIME.get(backend, _REALTIME["cpu"])[s.quality] * _CODEC_COST[s.codec] * pix * (1.8 if s.passes == 2 else 1.0)
    return Estimate(size, duration * factor + 1.5, kbps)


# ---------------------------------------------------------------------------
# Donanım algılama
# ---------------------------------------------------------------------------
_hw_override: Optional[Dict[str, bool]] = None


def set_hardware_override(hw: Optional[Dict[str, bool]]) -> None:
    """Testler için: ``{"nvenc": bool, "vaapi": bool}`` verir; ``None`` gerçek algılamaya döner."""
    global _hw_override
    _hw_override = hw


def _probe_encode(args: List[str], timeout: float = 20.0) -> bool:
    ff = find_binary("ffmpeg")
    if not ff:
        return False
    try:
        r = subprocess.run([ff, "-hide_banner", "-v", "error", "-y", *args], capture_output=True, timeout=timeout)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_hw_lock = threading.Lock()
_hw_result: Optional[Dict[str, bool]] = None
_hw_thread: Optional[threading.Thread] = None


def _detect_real() -> Dict[str, bool]:
    src = ["-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2:r=10"]
    has_nvidia = sys.platform == "win32" or Path("/dev/nvidiactl").exists() or Path("/proc/driver/nvidia").exists()   # Windows'ta aygıt dosyası yok: deneme kodlaması karar verir
    nv = _probe_encode(src + ["-c:v", "h264_nvenc", "-f", "null", "-"]) if has_nvidia else False
    va = _probe_encode(["-vaapi_device", VAAPI_DEVICE, *src, "-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-f", "null", "-"]) \
        if Path(VAAPI_DEVICE).exists() else False
    return {"nvenc": nv, "vaapi": va}


def _compute_hw() -> Dict[str, bool]:
    global _hw_result
    res = _detect_real()
    with _hw_lock:
        _hw_result = res
    return dict(res)


def detect_hardware(refresh: bool = False, block: bool = True) -> Dict[str, bool]:
    """Gerçekten kullanılabilen donanım kodlayıcılar (listede olmak yetmez: küçük bir deneme kodlaması yapılır).

    ``block=False`` (arayüz iş parçacığı): sonuç henüz yoksa arka planda algılamayı başlatır ve şimdilik "donanım yok" döner."""
    global _hw_result
    if _hw_override is not None:
        return {"nvenc": bool(_hw_override.get("nvenc")), "vaapi": bool(_hw_override.get("vaapi"))}
    if refresh:
        with _hw_lock:
            _hw_result = None
    with _hw_lock:
        cached = dict(_hw_result) if _hw_result is not None else None
    if cached is not None:
        return cached
    if not block:
        start_hardware_detection()
        return {"nvenc": False, "vaapi": False}
    return _compute_hw()


def start_hardware_detection(callback: Optional[Callable[[], None]] = None) -> None:
    """Algılamayı arka plan iş parçacığında başlatır (zaten sürüyorsa yenisini açmaz); bitince ``callback``."""
    global _hw_thread
    with _hw_lock:
        if _hw_thread is not None and _hw_thread.is_alive():
            return
        if _hw_result is not None and callback is None:
            return

        def run() -> None:
            _compute_hw()
            if callback:
                callback()
        _hw_thread = threading.Thread(target=run, name="kadraj-hw-detect", daemon=True)
        _hw_thread.start()


@dataclass(frozen=True)
class EncoderChoice:
    codec: str
    backend: str          # nvenc | vaapi | cpu
    name: str             # ffmpeg kodlayıcı adı


_ENCODER_NAMES = {("h264", "nvenc"): "h264_nvenc", ("h265", "nvenc"): "hevc_nvenc", ("h264", "vaapi"): "h264_vaapi",
                  ("h265", "vaapi"): "hevc_vaapi", ("h264", "cpu"): "libx264", ("h265", "cpu"): "libx265",
                  ("prores", "cpu"): "prores_ks", ("wav", "cpu"): "pcm_s24le"}


def choose_encoder(codec: str, hw: str = "auto", block: bool = True) -> EncoderChoice:
    """Öncelik NVENC → VAAPI → CPU. ProRes ve WAV her zaman CPU. ``hw`` belirli bir arka uç isterse ve yoksa CPU'ya düşer."""
    if codec in ("prores", "wav"):
        return EncoderChoice(codec, "cpu", _ENCODER_NAMES[(codec, "cpu")])
    avail = detect_hardware(block=block)
    order = [b for b in BACKENDS if (hw == "auto" or hw == b) and (b == "cpu" or avail.get(b))]
    backend = order[0] if order else "cpu"
    return EncoderChoice(codec, backend, _ENCODER_NAMES[(codec, backend)])


# ---------------------------------------------------------------------------
# Komut kurma
# ---------------------------------------------------------------------------
@dataclass
class RenderPlan:
    commands: List[List[str]]                 # sıralı komutlar (2 geçişte 2, aksi halde 1)
    output: str                               # nihai dosya
    part: str                                 # yazılan geçici dosya
    duration: float
    encoder: EncoderChoice
    width: int
    height: int
    fps: float
    graph: BuiltGraph
    estimate: Estimate
    warnings: List[str] = field(default_factory=list)
    temp: List[str] = field(default_factory=list)


_X264_PRESET = {"fast": "veryfast", "balanced": "medium", "heavy": "slow"}
_NV_PRESET = {"fast": "p2", "balanced": "p4", "heavy": "p6"}
_CRF = {"h264": {"fast": 24, "balanced": 21, "heavy": 18}, "h265": {"fast": 29, "balanced": 25, "heavy": 22}}
_CQ = {"fast": 26, "balanced": 23, "heavy": 20}
_QP = {"fast": 26, "balanced": 23, "heavy": 20}


def _chapters_file(tl: Timeline, duration: float) -> Optional[str]:
    ch = tl.chapters()
    if not ch:
        return None
    lines = [";FFMETADATA1"]
    for i, m in enumerate(ch):
        end = ch[i + 1].t if i + 1 < len(ch) else duration
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(m.t * 1000)}", f"END={max(int(end * 1000), int(m.t * 1000) + 1)}",
                  f"title={m.label or 'Bölüm'}"]
    fd, path = tempfile.mkstemp(prefix="kadraj-chap-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _rate_args(enc: EncoderChoice, s: RenderSettings, kbps: int, crf_mode: bool) -> List[str]:
    q = s.quality
    if enc.backend == "cpu":
        if enc.codec in ("h264", "h265"):
            args = ["-preset", _X264_PRESET[q]]
            if enc.codec == "h265":
                args += ["-tag:v", "hvc1"]
            if crf_mode:
                return args + ["-crf", str(_CRF[enc.codec][q])]
            if s.rate_mode == "cbr":
                return args + ["-b:v", f"{kbps}k", "-minrate", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k"]
            return args + ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.6)}k", "-bufsize", f"{kbps * 2}k"]
        return ["-profile:v", "3" if q != "heavy" else "4", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"]
    if enc.backend == "nvenc":
        args = ["-preset", _NV_PRESET[q]]
        if enc.codec == "h265":
            args += ["-tag:v", "hvc1"]
        if crf_mode:
            return args + ["-rc", "vbr", "-cq", str(_CQ[q]), "-b:v", "0"]
        rc = "cbr" if s.rate_mode == "cbr" else "vbr"
        return args + ["-rc", rc, "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k", "-bufsize", f"{kbps * 2}k"] + \
            (["-multipass", "fullres"] if s.passes == 2 else [])
    # vaapi
    args = ["-tag:v", "hvc1"] if enc.codec == "h265" else []
    if crf_mode:
        return args + ["-qp", str(_QP[q])]
    return args + ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * (1.0 if s.rate_mode == 'cbr' else 1.5))}k"]


def build_plan(tl: Timeline, settings: RenderSettings, output: str, *, info: Optional[InfoFn] = None,
               proxies: Optional[Dict[str, str]] = None, ffmpeg: Optional[str] = None) -> RenderPlan:
    """``tl``'yi ``output``'a işleyecek FFmpeg komutlarını kurar (çalıştırmaz)."""
    ff = ffmpeg or require_ffmpeg()
    s = settings
    src_tl = tl
    if s.t_start is not None or s.t_end is not None:
        t0, t1 = max(0.0, s.t_start or 0.0), min(tl.duration, s.t_end if s.t_end is not None else tl.duration)
        if t1 - t0 < 0.05:
            raise smart("bad_range")
        src_tl = window(tl, t0, t1)
    w = (s.width or tl.width) // 2 * 2
    h = (s.height or tl.height) // 2 * 2
    fps = s.fps or tl.fps
    work = copy.copy(src_tl)
    work.width, work.height, work.fps = w, h, fps
    audio_only = s.codec == "wav"
    g = build_graph(work, include_video=not audio_only, loudnorm_lufs=s.lufs, info=info, proxies=proxies)
    warnings = list(g.warnings)
    if s.max_seconds and g.duration > s.max_seconds:
        warnings.append(f"too_long:{int(s.max_seconds)}")
    enc = choose_encoder(s.codec, s.hw)
    kbps = effective_video_kbps(s, g.duration, w, h, fps)
    est = estimate(s, g.duration, w, h, fps, enc.backend)
    out = Path(output)
    part = str(out.with_name(out.name + ".part"))
    temp = list(g.temp_files)
    fc = g.filter_complex
    vmap = g.video_label
    pre: List[str] = []
    if not audio_only and enc.backend == "vaapi":
        pre = ["-vaapi_device", VAAPI_DEVICE]
        fc += ";\n[vout]format=nv12,hwupload[vhw]"
        vmap = "[vhw]"
    base = [ff, "-hide_banner", "-nostdin", "-y", "-v", "error", "-progress", "pipe:1", "-nostats", *pre, *g.input_args()]
    meta: List[str] = []
    if s.chapters and not audio_only:
        cf = _chapters_file(src_tl, g.duration)
        if cf:
            temp.append(cf)
            meta = ["-i", cf]
    maps = ["-filter_complex", fc]
    if not audio_only:
        maps += ["-map", vmap]
    if g.audio_label:
        maps += ["-map", g.audio_label]
    chap_maps = ["-map_metadata", str(len(g.inputs)), "-map_chapters", str(len(g.inputs))] if meta else []
    common_tail = ["-t", f"{g.duration:.3f}"]

    if audio_only:
        if not g.audio_label:
            raise smart("no_audio")
        cmd = base + maps + ["-c:a", "pcm_s24le", "-ar", "48000", "-f", "wav", *common_tail, part]
        return RenderPlan([cmd], str(out), part, g.duration, enc, w, h, fps, g, est, warnings, temp)

    crf_mode = enc.codec in ("h264", "h265") and not s.bitrate_kbps and s.rate_mode == "vbr" and s.passes == 1 \
        and s.max_size_mb <= 0
    two_pass = s.passes == 2 and enc.backend == "cpu" and enc.codec in ("h264", "h265")
    if s.passes == 2 and not two_pass and enc.backend == "vaapi":
        warnings.append("two_pass_unsupported")
    venc = ["-c:v", enc.name, *_rate_args(enc, s, kbps, crf_mode)]
    if enc.codec != "prores" and enc.backend == "cpu":
        venc += ["-pix_fmt", "yuv420p"]
    if enc.codec == "prores":
        aenc = ["-c:a", "pcm_s24le", "-ar", "48000"]
        fmt = ["-f", "mov"]
    else:
        aenc = ["-c:a", "aac", "-b:a", f"{s.audio_kbps}k", "-ar", "48000"]
        fmt = ["-f", "mp4", "-movflags", "+faststart"]
    fr = ["-r", fps_arg(fps)]
    if not g.audio_label:
        aenc = ["-an"]
    if two_pass:
        log = os.path.join(tempfile.gettempdir(), f"kadraj-pass-{os.getpid()}-{int(time.time() * 1000)}")
        temp += [log + "-0.log", log + "-0.log.mbtree", log + "-0.log.temp", log + "_2pass.log"]
        passflags = lambda n: ["-pass", str(n), "-passlogfile", log] if enc.codec == "h264" else \
            ["-x265-params", f"pass={n}:stats={log}.log"]
        p1 = base + maps + ["-an", *venc, *passflags(1), *fr, *common_tail, "-f", "null", os.devnull]
        p2 = base + meta + maps + [*chap_maps, *venc, *passflags(2), *aenc, *fr, *fmt, *common_tail, part]
        return RenderPlan([p1, p2], str(out), part, g.duration, enc, w, h, fps, g, est, warnings, temp)
    cmd = base + meta + maps + [*chap_maps, *venc, *aenc, *fr, *fmt, *common_tail, part]
    return RenderPlan([cmd], str(out), part, g.duration, enc, w, h, fps, g, est, warnings, temp)


# ---------------------------------------------------------------------------
# Çalıştırma
# ---------------------------------------------------------------------------
class RenderCancelled(Exception):
    pass


_OUT_TIME = re.compile(r"^out_time_(?:us|ms)=(\d+)")


def map_ffmpeg_error(stderr: str, returncode: int):
    low = stderr.lower()
    if "no space left" in low:
        return smart("disk_full", technical=stderr[-600:])
    if "unknown encoder" in low or "encoder not found" in low or "no such encoder" in low:
        return smart("no_encoder", technical=stderr[-600:])
    if "permission denied" in low:
        return smart("permission", technical=stderr[-600:])
    if "invalid data found" in low or "moov atom not found" in low:
        return smart("corrupt_input", technical=stderr[-600:])
    return smart("render_failed", technical=stderr[-800:] or f"çıkış kodu {returncode}", retryable=True)


def run_commands(commands: Sequence[Sequence[str]], duration: float, *, report: Optional[Callable[[float, str], None]] = None,
                 cancelled: Optional[Callable[[], bool]] = None, proc_holder: Optional[List[subprocess.Popen]] = None) -> None:
    """Komutları sırayla çalıştırır; ``-progress`` çıktısından 0..1 ilerleme bildirir. İptalde ``RenderCancelled``."""
    n = len(commands)
    for i, cmd in enumerate(commands):
        proc = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        if proc_holder is not None:
            proc_holder.append(proc)
        err_chunks: List[str] = []
        t = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read() if proc.stderr else ""), daemon=True)
        t.start()
        try:
            for line in proc.stdout:                                   # type: ignore[union-attr]
                if cancelled and cancelled():
                    proc.terminate()
                    raise RenderCancelled()
                m = _OUT_TIME.match(line)
                if m and duration > 0 and report:
                    done = min(1.0, int(m.group(1)) / 1e6 / duration)
                    report((i + done) / n, f"{int(((i + done) / n) * 100)}")
            proc.wait()
        except RenderCancelled:
            proc.wait(timeout=10)
            t.join(timeout=2)
            raise
        finally:
            if proc.poll() is None:
                proc.kill()
        t.join(timeout=5)
        if proc.returncode != 0:
            raise map_ffmpeg_error("".join(err_chunks), proc.returncode)


def execute_plan(plan: RenderPlan, *, report: Optional[Callable[[float, str], None]] = None,
                 cancelled: Optional[Callable[[], bool]] = None) -> str:
    """Planı çalıştırır, ``.part`` dosyasını nihai ada çevirir ve yolu döner. Hata/iptalde geçici dosyalar silinir."""
    Path(plan.output).parent.mkdir(parents=True, exist_ok=True)
    try:
        run_commands(plan.commands, plan.duration, report=report, cancelled=cancelled)
        os.replace(plan.part, plan.output)
    except BaseException:
        try:
            os.unlink(plan.part)
        except OSError:
            pass
        raise
    finally:
        plan.graph.cleanup()
        for f in plan.temp:
            try:
                os.unlink(f)
            except OSError:
                pass
    if report:
        report(1.0, "100")
    return plan.output
