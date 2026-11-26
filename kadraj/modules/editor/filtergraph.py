"""``Timeline`` → FFmpeg ``-filter_complex`` (video bindirme + ses karışımı). Saf mantık: ``ffmpeg`` çalıştırmaz.

Genel plan
----------
* Tuval: ``color`` kaynağı (siyah, ``W×H@fps``). ``tracks`` listesinde **sonraki kanal üsttedir**.
* Video/resim/dondur klibi: ``trim`` → hız (``setpts``; eğri varsa sabit hızlı parçalar + ``concat``) → ``fps/scale`` sığdır →
  klip efektleri → keyframe'li ``scale``/``rotate``/opaklık → solma → ``overlay`` (``enable='between(t,a,b)'``).
  ``setpts`` klibi zaman çizelgesi zamanına taşır, bu yüzden filtre ifadelerinde ``t`` **mutlak** zamandır.
* ADJ (ayar katmanı) ve ``fx`` kanalları: o ana kadar bindirilmiş tuvale efekt zincirini ``enable`` penceresiyle uygular.
* Ses: klip başına ``atrim/atempo/volume/afade/adelay`` → kanal ``amix`` → kanal ses/pan/ducking (``sidechaincompress``) →
  ana ``amix`` → ana ses + 10 bant EQ + isteğe bağlı ``loudnorm``/``alimiter``.
Keyframe'ler Bezier eğrisinden örneklenip parçalı doğrusal FFmpeg ifadesine çevrilir (``pw_expr``).
"""
from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ... import device, paths
from .errors import smart
from .probe import MediaInfo, probe
from .timeline import (Clip, EQ_BANDS, Keyframe, Timeline, Track, fps_value, interpolate, sample_keyframes)

AUDIO_RATE = 48000
MAX_SEGMENTS = 24
MAX_EXPR_POINTS = 48


# ---------------------------------------------------------------------------
# Küçük yardımcılar
# ---------------------------------------------------------------------------
def num(v: float) -> str:
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def fps_arg(fps: float) -> str:
    f = fps_value(fps)
    for n in (24000, 30000, 60000):
        if abs(f - n / 1001) < 1e-6:
            return f"{n}/1001"
    return str(int(round(f))) if abs(f - round(f)) < 1e-6 else num(f)


#: Android'de fontconfig yoktur: libass (altyazı) ve drawtext (yazı) yalnız uygulamanın kendi yazı tiplerini
#: (``paths.fonts_dir()``) görür. Bu yüzden orada aile adı ve dosya açıkça verilir.
ANDROID_FONT_FAMILY = "Inter"
ANDROID_FONT_FILE = "Inter-SemiBold.ttf"


def subtitle_font() -> str:
    """Altyazı stilinin yazı tipi ailesi: masaüstünde "Sans" (fontconfig seçer), Android'de uygulamadaki Inter."""
    return ANDROID_FONT_FAMILY if device.is_android() else "Sans"


def android_subtitle_args(style: str, srt: bool) -> Tuple[str, str]:
    """Android'de ``subtitles`` süzgecine eklenecek ``:fontsdir=…`` ve gerekirse ``FontName``'li stil.

    ``.srt`` için stil yazı tipi adı vermiyorsa uygulamanın ailesi eklenir (yoksa libass "Arial"ı bulamaz ve yazı
    çıkmaz). ``.ass`` dosyalarının kendi stilleri olduğundan onlara dokunulmaz. Masaüstünde ``("", style)`` döner.
    """
    if not device.is_android():
        return "", style
    if srt and "fontname" not in style.lower():
        style = ",".join(x for x in (f"FontName={ANDROID_FONT_FAMILY}", style) if x)
    return f":fontsdir={quote(str(paths.fonts_dir()))}", style


def default_text_font() -> str:
    """``drawtext`` için yazı tipi dosyası verilmemişse: masaüstünde boş (fontconfig), Android'de uygulamadaki Inter."""
    return str(paths.fonts_dir() / ANDROID_FONT_FILE) if device.is_android() else ""


def quote(text: str) -> str:
    """Filtre seçeneği için tek tırnaklı güvenli değer (yol/metin)."""
    return "'" + text.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:") + "'"


def pw_expr(points: Sequence[Tuple[float, float]], var: str = "t", offset: float = 0.0) -> str:
    """``(t, v)`` noktalarından parçalı doğrusal FFmpeg ifadesi (``t`` + ``offset`` mutlak zamana çevirir)."""
    pts = sorted((float(a) + offset, float(b)) for a, b in points)
    if len(pts) > MAX_EXPR_POINTS:                                    # çok yoğunsa seyrelt
        step = len(pts) / MAX_EXPR_POINTS
        pts = [pts[min(len(pts) - 1, int(i * step))] for i in range(MAX_EXPR_POINTS - 1)] + [pts[-1]]
    if len(pts) == 1:
        return num(pts[0][1])
    expr = num(pts[-1][1])
    for (ta, va), (tb, vb) in reversed(list(zip(pts, pts[1:]))):
        seg = num(va) if tb - ta < 1e-6 else f"{num(va)}+({num(vb - va)})*({var}-{num(ta)})/{num(tb - ta)}"
        expr = f"if(lt({var},{num(tb)}),{seg},{expr})"
    return f"if(lt({var},{num(pts[0][0])}),{num(pts[0][1])},{expr})"


def kf_expr(kfs: Sequence[Keyframe], var: str = "t", offset: float = 0.0) -> str:
    return pw_expr(sample_keyframes(kfs, per_segment=6), var, offset)


def is_const(kfs: Sequence[Keyframe]) -> bool:
    return len(kfs) == 1 or len({round(k.v, 6) for k in kfs}) == 1


def atempo_chain(speed: float) -> List[str]:
    out: List[str] = []
    s = max(0.1, min(16.0, speed))
    while s < 0.5:
        out.append("atempo=0.5")
        s /= 0.5
    while s > 2.0:
        out.append("atempo=2.0")
        s /= 2.0
    if abs(s - 1.0) > 1e-6:
        out.append(f"atempo={num(s)}")
    return out


def speed_segments(clip: Clip, max_segments: int = MAX_SEGMENTS) -> List[Tuple[float, float, float]]:
    """``[(kaynak_in, kaynak_out, hız)]``. Eğri yoksa tek parça; varsa sabit hızlı ≤``max_segments`` parça."""
    if not clip.speed_curve:
        return [(clip.in_, clip.out, max(0.1, min(16.0, clip.speed)))]
    dur = clip.duration
    n = max(1, min(max_segments, int(math.ceil(dur / 0.5))))
    edges = [clip.source_time(dur * i / n) for i in range(n)] + [clip.out]
    segs: List[Tuple[float, float, float]] = []
    for i in range(n):
        a, b = edges[i], edges[i + 1]
        if b - a > 1e-4:
            segs.append((a, b, clip.speed_at(dur * (i + 0.5) / n)))
    return segs or [(clip.in_, clip.out, 1.0)]


# ---------------------------------------------------------------------------
# Efektler
# ---------------------------------------------------------------------------
def effect_filter(eff: Dict, enable: Optional[str] = None) -> Optional[str]:
    """Tek efekti FFmpeg filtresine çevirir (``None``: bilinmeyen/kapalı). ``enable`` yalnızca zaman çizelgesi destekleyenlere eklenir."""
    if not eff.get("enabled", True):
        return None
    typ, p = str(eff.get("type", "")), eff.get("params") or {}

    def f(key: str, default: float) -> float:
        try:
            return float(p.get(key, default))
        except (TypeError, ValueError):
            return default
    en = f":enable='{enable}'" if enable else ""
    if typ == "eq":
        return (f"eq=brightness={num(max(-1, min(1, f('brightness', 0))))}:contrast={num(max(0, min(4, f('contrast', 1))))}"
                f":saturation={num(max(0, min(3, f('saturation', 1))))}:gamma={num(max(0.1, min(10, f('gamma', 1))))}{en}")
    if typ == "hue":
        return f"hue=h={num(f('degrees', 0))}:s={num(max(0, min(10, f('saturation', 1))))}{en}"
    if typ == "grayscale":
        return f"hue=s=0{en}"
    if typ == "blur":
        return f"gblur=sigma={num(max(0.1, min(60, f('amount', 4))))}{en}"
    if typ == "sharpen":
        return f"unsharp=5:5:{num(max(0, min(5, f('amount', 1))))}{en}"
    if typ == "denoise":
        return f"hqdn3d={num(f('luma', 4))}:{num(f('chroma', 3))}:6:4.5{en}"
    if typ == "vignette":
        return f"vignette=angle={num(max(0.1, min(1.57, f('angle', 0.6))))}{en}"
    if typ == "lut" and p.get("file"):
        return f"lut3d=file={quote(str(p['file']))}:interp=tetrahedral{en}"
    if typ == "colorbalance":
        keys = [f"{c}{z}={num(max(-1, min(1, f(c + z, 0))))}" for z in "smh" for c in "rgb" if f(c + z, 0)]
        return f"colorbalance={':'.join(keys)}{en}" if keys else None
    if typ == "curves" and p.get("preset"):
        return f"curves=preset={str(p['preset'])}{en}"
    if typ == "chromakey":
        color = str(p.get("color", "0x00ff00")).replace("#", "0x")
        return f"chromakey=color={color}:similarity={num(f('similarity', 0.15))}:blend={num(f('blend', 0.1))}"
    if typ == "hflip":
        return "hflip"
    if typ == "vflip":
        return "vflip"
    return None


def effect_chain(effects: Sequence[Dict], enable: Optional[str] = None) -> Tuple[List[str], List[str]]:
    out: List[str] = []
    unknown: List[str] = []
    for e in effects:
        s = effect_filter(e, enable)
        if s:
            out.append(s)
        elif e.get("enabled", True) and e.get("type") not in (None, "duck", "eq10"):
            unknown.append(str(e.get("type")))
    return out, unknown


# ---------------------------------------------------------------------------
# Pencere (kısmi işleme: tek kare önizleme, aralık dışa aktarma)
# ---------------------------------------------------------------------------
def window(tl: Timeline, t0: float, t1: float) -> Timeline:
    """``[t0, t1)`` aralığını yeni bir zaman çizelgesi olarak kopyalar (zaman 0'a kaydırılır). Anahtar kareler/fade korunur."""
    out = Timeline.from_dict(tl.to_dict(), [m.to_dict() for m in tl.markers])
    out.markers = [m for m in out.markers if t0 <= m.t < t1]
    for m in out.markers:
        m.t -= t0
    for tr in out.tracks:
        kept: List[Clip] = []
        for c in tr.clips:
            if c.end <= t0 or c.start >= t1:
                continue
            delta = max(0.0, t0 - c.start)
            keep_dur = min(c.end, t1) - max(c.start, t0)
            if delta > 0:
                if c.kind in ("adjust", "text", "freeze") or (c.kind == "image" and c.out <= c.in_):
                    c.duration_hint -= delta
                else:
                    c.in_ = c.source_time(delta)
                for name, kfs in c.keyframes.items():
                    v0 = interpolate(kfs, delta)
                    c.keyframes[name] = [Keyframe(0.0, float(v0))] + [Keyframe(k.t - delta, k.v, k.ease, k.bezier) for k in kfs if k.t > delta]
                if c.speed_curve:
                    v0 = interpolate(c.speed_curve, delta)
                    c.speed_curve = [Keyframe(0.0, float(v0))] + [Keyframe(k.t - delta, k.v, k.ease, k.bezier) for k in c.speed_curve if k.t > delta]
                c.fade_in = max(0.0, c.fade_in - delta)
                c.start = 0.0
            else:
                c.start -= t0
            if c.start + c.duration > keep_dur + c.start + 1e-6 or keep_dur < c.duration - 1e-6:
                if c.kind in ("adjust", "text", "freeze") or (c.kind == "image" and c.out <= c.in_):
                    c.duration_hint = keep_dur
                else:
                    c.out = c.source_time(keep_dur)
                c.fade_out = 0.0 if c.end > keep_dur + c.start + 1e-6 and c.fade_out > keep_dur else c.fade_out
            kept.append(c)
        tr.clips = kept
    return out


# ---------------------------------------------------------------------------
# Çıktı yapısı
# ---------------------------------------------------------------------------
@dataclass
class BuiltGraph:
    inputs: List[Tuple[str, List[str]]] = field(default_factory=list)    # (yol, giriş öncesi argümanlar)
    filter_complex: str = ""
    video_label: Optional[str] = None
    audio_label: Optional[str] = None
    duration: float = 0.0
    warnings: List[str] = field(default_factory=list)
    temp_files: List[str] = field(default_factory=list)

    def input_args(self) -> List[str]:
        args: List[str] = []
        for path, pre in self.inputs:
            args += pre + ["-i", path]
        return args

    def cleanup(self) -> None:
        for f in self.temp_files:
            try:
                os.unlink(f)
            except OSError:
                pass
        self.temp_files.clear()


InfoFn = Callable[[str], MediaInfo]


class _Builder:
    def __init__(self, tl: Timeline, info: InfoFn, include_video: bool, include_audio: bool, size: Optional[Tuple[int, int]],
                 loudnorm_lufs: Optional[float], proxies: Optional[Dict[str, str]], limiter: bool) -> None:
        self.tl, self.info = tl, info
        self.iv, self.ia = include_video, include_audio
        self.W, self.H = size or (tl.width, tl.height)
        self.W, self.H = max(2, self.W // 2 * 2), max(2, self.H // 2 * 2)
        self.fps = fps_arg(tl.fps)
        self.lufs, self.proxies, self.limiter = loudnorm_lufs, proxies or {}, limiter
        self.g = BuiltGraph(duration=tl.duration)
        self.lines: List[str] = []
        self._n = 0

    # -- yardımcılar --------------------------------------------------------------------------
    def lab(self, prefix: str = "x") -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def add_input(self, path: str, pre: Optional[List[str]] = None) -> int:
        self.g.inputs.append((path, pre or []))
        return len(self.g.inputs) - 1

    def src(self, clip: Clip) -> str:
        return self.proxies.get(clip.src, clip.src)

    def media(self, clip: Clip) -> Optional[MediaInfo]:
        try:
            return self.info(clip.src)
        except Exception as exc:
            if hasattr(exc, "code"):
                raise
            self.g.warnings.append(f"{clip.src}: {exc}")
            return None

    # -- ana akış -----------------------------------------------------------------------------
    def build(self) -> BuiltGraph:
        tl = self.tl
        if tl.duration <= 0:
            raise smart("empty_timeline")
        if self.iv:
            self.video()
        if self.ia:
            self.audio()
        self.g.filter_complex = ";\n".join(self.lines)
        return self.g

    # -- video --------------------------------------------------------------------------------
    def video(self) -> None:
        tl = self.tl
        cur = self.lab("bg")
        self.lines.append(f"color=c=black:s={self.W}x{self.H}:r={self.fps}:d={num(tl.duration)},format=yuv420p[{cur}]")
        soloed = any(t.solo for t in tl.tracks if t.kind == "video")
        for tr in tl.tracks:
            if tr.hidden or tr.kind == "audio" or (soloed and tr.kind == "video" and not tr.solo):
                continue
            for clip in tr.sorted_clips():
                if not clip.enabled:
                    continue
                if tr.kind in ("adj", "fx") or clip.kind == "adjust":
                    cur = self.adjust(cur, clip)
                elif clip.kind == "text" or tr.kind == "sub":
                    cur = self.text(cur, clip)
                elif tr.kind == "video":
                    cur = self.layer(cur, clip)
        self.lines.append(f"[{cur}]format=yuv420p[vout]")
        self.g.video_label = "[vout]"

    def adjust(self, cur: str, clip: Clip) -> str:
        enable = f"between(t,{num(clip.start)},{num(clip.end)})"
        chain, unknown = effect_chain(clip.effects, enable)
        self.g.warnings += [f"efekt tanınmadı: {u}" for u in unknown]
        if not chain:
            return cur
        out = self.lab("adj")
        self.lines.append(f"[{cur}]{','.join(chain)}[{out}]")
        return out

    def text(self, cur: str, clip: Clip) -> str:
        d = clip.data or {}
        enable = f"between(t,{num(clip.start)},{num(clip.end)})"
        out = self.lab("txt")
        if d.get("ass") or d.get("srt"):
            path = str(d.get("ass") or d.get("srt"))
            fonts, style_text = android_subtitle_args(str(d.get("style") or "") if d.get("srt") else "", bool(d.get("srt")))
            style = f":force_style={quote(style_text)}" if style_text else ""
            self.lines.append(f"[{cur}]subtitles=filename={quote(path)}{fonts}{style}[{out}]")
            return out
        text = str(d.get("text", clip.name or ""))
        if not text:
            return cur
        fd, tmp = tempfile.mkstemp(prefix="kadraj-txt-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.g.temp_files.append(tmp)
        size = max(8, int(float(d.get("size", 0.06)) * self.H))
        color = str(d.get("color", "white")).replace("#", "0x")
        x = d.get("x"), d.get("y")
        px = f"(w-text_w)/2" if x[0] is None else f"{num(float(x[0]) * self.W)}"
        py = f"h-text_h-{num(self.H * 0.08)}" if x[1] is None else f"{num(float(x[1]) * self.H)}"
        font_file = str(d.get("font") or default_text_font())
        font = f":fontfile={quote(font_file)}" if font_file else ""
        box = f":box=1:boxcolor=black@0.45:boxborderw={max(4, size // 4)}" if d.get("box", True) else ""
        self.lines.append(f"[{cur}]drawtext=textfile={quote(tmp)}:expansion=none{font}:fontsize={size}:fontcolor={color}:x='{px}':y='{py}'{box}"
                          f":enable='{enable}'[{out}]")
        return out

    def source_chain(self, clip: Clip, idx: int, info: Optional[MediaInfo]) -> str:
        """Kaynak görüntüyü hız parçalarıyla ``setpts``'lı tek akışa indirir; etiketi döner."""
        out = self.lab("s")
        if clip.kind == "freeze":
            self.lines.append(f"[{idx}:v]trim=start={num(clip.in_)}:end={num(clip.in_ + 2 / fps_value(self.tl.fps))},"
                              f"setpts=PTS-STARTPTS,select='eq(n,0)',tpad=stop_mode=clone:stop_duration={num(clip.duration)},"
                              f"setpts=PTS-STARTPTS+{num(clip.start)}/TB[{out}]")
            return out
        if clip.kind == "image" or (info is not None and info.is_image):
            self.lines.append(f"[{idx}:v]trim=duration={num(clip.duration)},setpts=PTS-STARTPTS+{num(clip.start)}/TB[{out}]")
            return out
        segs = speed_segments(clip)
        if len(segs) == 1:
            a, b, s = segs[0]
            setpts = "PTS-STARTPTS" if abs(s - 1) < 1e-6 else f"(PTS-STARTPTS)/{num(s)}"
            self.lines.append(f"[{idx}:v]trim=start={num(a)}:end={num(b)},setpts={setpts}+{num(clip.start)}/TB[{out}]")
            return out
        split = [self.lab("sp") for _ in segs]
        self.lines.append(f"[{idx}:v]split={len(segs)}" + "".join(f"[{x}]" for x in split))
        parts = []
        for x, (a, b, s) in zip(split, segs):
            p = self.lab("sg")
            self.lines.append(f"[{x}]trim=start={num(a)}:end={num(b)},setpts=(PTS-STARTPTS)/{num(s)}[{p}]")
            parts.append(p)
        self.lines.append("".join(f"[{p}]" for p in parts) + f"concat=n={len(parts)}:v=1:a=0,setpts=PTS-STARTPTS+{num(clip.start)}/TB[{out}]")
        return out

    def layer(self, cur: str, clip: Clip) -> str:
        info = self.media(clip) if clip.kind in ("media", "freeze") else None
        if clip.kind in ("media", "freeze") and info is not None and not info.has_video:
            return cur                                                    # yalnızca ses dosyası: görüntü yok
        if clip.kind == "image":
            idx = self.add_input(self.src(clip), ["-loop", "1", "-framerate", self.fps.replace("/1001", "") if "/" not in self.fps else self.fps,
                                                   "-t", num(clip.duration + 1)])
        else:
            idx = self.add_input(self.src(clip))
        label = self.source_chain(clip, idx, info)
        chain = [f"fps={self.fps}", f"scale={self.W}:{self.H}:force_original_aspect_ratio=decrease", "setsar=1", "format=yuva420p"]
        eff, unknown = effect_chain(clip.effects)
        self.g.warnings += [f"efekt tanınmadı: {u}" for u in unknown]
        chain += eff
        # dönüşümler (keyframe'li ya da sabit)
        k = clip.keyframes
        off = clip.start
        if k.get("rotate") and not (is_const(k["rotate"]) and abs(k["rotate"][0].v) < 1e-6):
            e = kf_expr(k["rotate"], "t", off) if not is_const(k["rotate"]) else num(k["rotate"][0].v)
            chain.append(f"rotate=a='({e})*PI/180':c=none:ow='hypot(iw,ih)':oh='hypot(iw,ih)'")
        if k.get("opacity"):
            if is_const(k["opacity"]):
                if abs(k["opacity"][0].v - 1) > 1e-6:
                    chain.append(f"colorchannelmixer=aa={num(max(0, min(1, k['opacity'][0].v)))}")
            else:
                e = kf_expr(k["opacity"], "T", off)
                chain.append(f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='alpha(X,Y)*clip({e},0,1)'")
        if clip.fade_in > 0:
            chain.append(f"fade=t=in:st={num(clip.start)}:d={num(clip.fade_in)}:alpha=1")
        if clip.fade_out > 0:
            chain.append(f"fade=t=out:st={num(clip.end - clip.fade_out)}:d={num(clip.fade_out)}:alpha=1")
        # yakınlaştırma **en sonda**: kare boyutu her karede değişir; araya giren süzgeçler (rotate/geq/fade) sabit boyut ister (FFmpeg ≥ 9.1)
        if k.get("scale") and not (is_const(k["scale"]) and abs(k["scale"][0].v - 1) < 1e-6):
            e = kf_expr(k["scale"], "t", off) if not is_const(k["scale"]) else num(k["scale"][0].v)
            chain.append(f"scale=w='max(2,trunc(iw*({e})/2)*2)':h='max(2,trunc(ih*({e})/2)*2)':eval=frame")
            chain.append("format=yuva420p")
        prepared = self.lab("v")
        self.lines.append(f"[{label}]{','.join(chain)}[{prepared}]")
        ox = f"(W-w)/2+({kf_expr(k['x'], 't', off)})*W" if k.get("x") and not is_const(k["x"]) else \
            f"(W-w)/2+({num(k['x'][0].v)})*W" if k.get("x") else "(W-w)/2"
        oy = f"(H-h)/2+({kf_expr(k['y'], 't', off)})*H" if k.get("y") and not is_const(k["y"]) else \
            f"(H-h)/2+({num(k['y'][0].v)})*H" if k.get("y") else "(H-h)/2"
        out = self.lab("o")
        self.lines.append(f"[{cur}][{prepared}]overlay=x='{ox}':y='{oy}':eof_action=pass:eval=frame:"
                          f"enable='between(t,{num(clip.start)},{num(clip.end)})'[{out}]")
        return out

    # -- ses ----------------------------------------------------------------------------------
    def audible_tracks(self) -> List[Track]:
        tl = self.tl
        soloed = any(t.solo for t in tl.tracks)
        return [t for t in tl.tracks if t.kind in ("audio", "video") and not t.muted and (not soloed or t.solo)]

    def clip_audio(self, tr: Track, clip: Clip) -> Optional[str]:
        if clip.kind not in ("media", "freeze") or not clip.enabled or clip.kind == "freeze":
            return None
        info = self.media(clip)
        if info is None or not info.has_audio or clip.data.get("audio_muted"):
            return None
        idx = self.add_input(self.src(clip))
        segs = speed_segments(clip)
        parts = []
        ain = f"[{idx}:a]"
        if len(segs) > 1:
            split = [self.lab("as") for _ in segs]
            self.lines.append(f"{ain}asplit={len(segs)}" + "".join(f"[{x}]" for x in split))
            ins = [f"[{x}]" for x in split]
        else:
            ins = [ain]
        for pin, (a, b, s) in zip(ins, segs):
            p = self.lab("ag")
            chain = [f"atrim=start={num(a)}:end={num(b)}", "asetpts=PTS-STARTPTS"] + atempo_chain(s)
            self.lines.append(f"{pin}{','.join(chain)}[{p}]")
            parts.append(p)
        cur = parts[0]
        if len(parts) > 1:
            cur = self.lab("ac")
            self.lines.append("".join(f"[{p}]" for p in parts) + f"concat=n={len(parts)}:v=0:a=1[{cur}]")
        chain = [f"aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo"]
        vk = clip.keyframes.get("volume")
        if vk and not is_const(vk):
            chain.append(f"volume=volume='{kf_expr(vk, 't', 0.0)}':eval=frame")
        elif vk:
            chain.append(f"volume={num(vk[0].v)}")
        if abs(clip.volume_db) > 1e-6:
            chain.append(f"volume={num(clip.volume_db)}dB")
        dur = clip.duration
        if clip.fade_in > 0:
            chain.append(f"afade=t=in:st=0:d={num(clip.fade_in)}")
        if clip.fade_out > 0:
            chain.append(f"afade=t=out:st={num(max(0.0, dur - clip.fade_out))}:d={num(clip.fade_out)}")
        ms = int(round(clip.start * 1000))
        if ms > 0:
            chain.append(f"adelay=delays={ms}:all=1")
        out = self.lab("ca")
        self.lines.append(f"[{cur}]{','.join(chain)}[{out}]")
        return out

    def audio(self) -> None:
        tl = self.tl
        buses: Dict[str, str] = {}
        for tr in self.audible_tracks():
            labels = [x for x in (self.clip_audio(tr, c) for c in tr.sorted_clips()) if x]
            if not labels:
                continue
            bus = labels[0]
            if len(labels) > 1:
                bus = self.lab("tb")
                self.lines.append("".join(f"[{x}]" for x in labels) +
                                  f"amix=inputs={len(labels)}:normalize=0:duration=longest:dropout_transition=0[{bus}]")
            post: List[str] = []
            mix_map = {"mono": "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1", "left": "pan=stereo|c0=c0|c1=c0",
                       "right": "pan=stereo|c0=c1|c1=c1", "swap": "pan=stereo|c0=c1|c1=c0"}
            if tr.channels in mix_map:
                post.append(mix_map[tr.channels])
            if abs(tr.volume_db) > 1e-6:
                post.append(f"volume={num(tr.volume_db)}dB")
            if abs(tr.pan) > 1e-6:
                l, r = (1.0, 1.0 - tr.pan) if tr.pan > 0 else (1.0 + tr.pan, 1.0)
                post.append(f"pan=stereo|c0={num(l)}*c0|c1={num(r)}*c1")
            if post:
                o = self.lab("tp")
                self.lines.append(f"[{bus}]{','.join(post)}[{o}]")
                bus = o
            buses[tr.id] = bus
        if not buses:
            self.g.audio_label = None
            return
        # ducking: kanalın "duck" efekti anahtar kanallarla sidechaincompress yapar
        key_ids = {k for tr in self.audible_tracks() for e in tr.effects if e.get("type") == "duck" and e.get("enabled", True)
                   for k in (e.get("params") or {}).get("keys", [])}
        keys: Dict[str, str] = {}
        for kid in key_ids:
            if kid in buses:
                a, b = self.lab("kd"), self.lab("kk")
                self.lines.append(f"[{buses[kid]}]asplit=2[{a}][{b}]")
                buses[kid], keys[kid] = a, b
        for tr in self.audible_tracks():
            for e in tr.effects:
                if e.get("type") != "duck" or not e.get("enabled", True) or tr.id not in buses:
                    continue
                p = e.get("params") or {}
                ks = [keys[k] for k in p.get("keys", []) if k in keys]
                if not ks:
                    continue
                key = ks[0]
                if len(ks) > 1:
                    key = self.lab("km")
                    self.lines.append("".join(f"[{k}]" for k in ks) + f"amix=inputs={len(ks)}:normalize=0[{key}]")
                o = self.lab("dk")
                self.lines.append(f"[{buses[tr.id]}][{key}]sidechaincompress=threshold={num(p.get('threshold', 0.05))}:ratio={num(p.get('ratio', 8))}"
                                  f":attack={num(p.get('attack', 20))}:release={num(p.get('release', 400))}:makeup=1[{o}]")
                buses[tr.id] = o
        ids = list(buses)
        mix = buses[ids[0]]
        if len(ids) > 1:
            mix = self.lab("mx")
            self.lines.append("".join(f"[{buses[i]}]" for i in ids) + f"amix=inputs={len(ids)}:normalize=0:duration=longest[{mix}]")
        master: List[str] = []
        if abs(tl.master_db) > 1e-6:
            master.append(f"volume={num(tl.master_db)}dB")
        for f, g in zip(EQ_BANDS, tl.eq):
            if abs(g) > 0.05:
                master.append(f"equalizer=f={f}:t=o:w=1:g={num(max(-18, min(18, g)))}")
        if self.lufs is not None:
            master.append(f"loudnorm=I={num(self.lufs)}:TP=-1.5:LRA=11")
        if self.limiter:
            master.append("alimiter=limit=0.95")
        master.append(f"aformat=sample_rates={AUDIO_RATE}:channel_layouts=stereo")
        self.lines.append(f"[{mix}]{','.join(master)},atrim=end={num(tl.duration)}[aout]")
        self.g.audio_label = "[aout]"


def build_graph(tl: Timeline, *, include_video: bool = True, include_audio: bool = True, size: Optional[Tuple[int, int]] = None,
                info: Optional[InfoFn] = None, loudnorm_lufs: Optional[float] = None, proxies: Optional[Dict[str, str]] = None,
                limiter: bool = True) -> BuiltGraph:
    """Zaman çizelgesini FFmpeg grafiğine çevirir. ``size``: önizleme/proxy çözünürlüğü; ``proxies``: ``src → proxy yolu``."""
    return _Builder(tl, info or probe, include_video, include_audio, size, loudnorm_lufs, proxies, limiter).build()
