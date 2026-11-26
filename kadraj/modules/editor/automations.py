"""İsteğe bağlı, **kullanıcı tetiklemeli** ve **geri alınabilir** otomasyonlar.

Kural (plan §0): hiçbiri kendiliğinden çalışmaz. Her otomasyon önce **analiz eder** ve bir ``Proposal`` döner (ne yapacağını
anlatan özet + ``apply(timeline)`` fonksiyonu). ``EditorService.apply(proposal)`` bunu tek bir geri alınabilir komut olarak
uygular. Analiz zaman çizelgesini değiştirmez. Ağır paketler (librosa, OpenCV, rembg) yoksa ``SmartError`` +
"İsteğe bağlı paketi kur" eylemi atılır; kod yolu içe aktarma hatasına karşı korumalıdır.
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import color, frames, ops
from .errors import install_hint, smart
from .filtergraph import fps_arg, num, subtitle_font
from .probe import probe, require_ffmpeg
from .timeline import Clip, Keyframe, Timeline, Track, new_id


@dataclass
class Proposal:
    """Bir otomasyonun önerisi: ``apply`` çağrılınca zaman çizelgesi değişir (servis bunu undo komutuna sarar)."""
    name: str                                   # i18n anahtarı: ``editor.auto.<name>``
    summary: str
    apply: Callable[[Timeline], None]
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return bool(self.details.get("empty"))


def _clip(tl: Timeline, clip_id: Optional[str]) -> Tuple[Track, Clip]:
    if not clip_id:
        raise smart("no_clip")
    found = tl.find_clip(clip_id)
    if found is None:
        raise smart("no_clip")
    return found


def _optional(module: str, key: str, packages: str):
    try:
        mod = importlib.import_module(module)
    except Exception:
        raise install_hint(key, packages) from None
    if mod is None:
        raise install_hint(key, packages)
    return mod


# ===========================================================================================
# Genel: aralık kesme (sessizlik kesici + metinle kurgu ortak)
# ===========================================================================================
def keep_ranges(lo: float, hi: float, cuts: Sequence[Tuple[float, float]], min_keep: float = 0.05) -> List[Tuple[float, float]]:
    """``[lo, hi]`` içinden ``cuts`` aralıklarını çıkarır; kalan parçaları (en az ``min_keep`` sn) döner."""
    out: List[Tuple[float, float]] = []
    cur = lo
    for a, b in sorted(cuts):
        a, b = max(lo, a), min(hi, b)
        if b <= a:
            continue
        if a - cur >= min_keep:
            out.append((cur, a))
        cur = max(cur, b)
    if hi - cur >= min_keep:
        out.append((cur, hi))
    return out


def cut_ranges_apply(clip_id: str, cuts: Sequence[Tuple[float, float]]) -> Callable[[Timeline], None]:
    """Kaynak zamanındaki ``cuts`` aralıklarını klipten çıkarır; kalan parçalar ardışık dizilir ve sonraki klipler öne çekilir
    (ripple). Kilitli kanalda ``LockedError``."""
    def apply(tl: Timeline) -> None:
        tr, c = tl.find_clip(clip_id) or (None, None)
        if c is None or tr is None:
            raise ops.EditError("klip yok")
        if tr.locked:
            raise ops.LockedError(tr.id)
        parts = keep_ranges(c.in_, c.out, cuts)
        if not parts:
            return
        old_end, start = c.end, c.start
        speed = max(0.1, c.speed)
        tr.clips.remove(c)
        t = start
        pieces: List[Clip] = []
        for i, (a, b) in enumerate(parts):
            piece = c.copy(c.id if i == 0 else new_id())
            piece.in_, piece.out, piece.start = a, b, t
            piece.speed_curve = []
            piece.fade_in = c.fade_in if i == 0 else 0.0
            piece.fade_out = c.fade_out if i == len(parts) - 1 else 0.0
            pieces.append(piece)
            t += (b - a) / speed
        removed = old_end - t
        for o in tr.clips:
            if o.start >= old_end - 1e-6:
                o.start = max(0.0, o.start - removed)
        tr.clips.extend(pieces)
    return apply


# ===========================================================================================
# 1) Sessizlik kesici
# ===========================================================================================
_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def parse_silencedetect(stderr: str) -> List[Tuple[float, float]]:
    starts = [float(x) for x in _SIL_START.findall(stderr)]
    ends = [float(x) for x in _SIL_END.findall(stderr)]
    out: List[Tuple[float, float]] = []
    for i, s in enumerate(starts):
        out.append((max(0.0, s), ends[i] if i < len(ends) else float("inf")))
    return out


def detect_silences(path: str, *, noise_db: float = -35.0, min_silence: float = 0.5, start: float = 0.0,
                    duration: Optional[float] = None) -> List[Tuple[float, float]]:
    """Kaynak zamanında sessiz aralıklar (``silencedetect``)."""
    ff = require_ffmpeg()
    cmd = [ff, "-hide_banner", "-nostdin"] + (["-ss", f"{start:.3f}"] if start else []) + \
        (["-t", f"{duration:.3f}"] if duration else []) + ["-i", path, "-vn", "-af",
                                                           f"silencedetect=noise={num(noise_db)}dB:d={num(min_silence)}", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return [(a + start, (b + start) if b != float("inf") else float("inf")) for a, b in parse_silencedetect(r.stderr)]


def plan_silence_cut(tl: Timeline, clip_id: str, *, noise_db: float = -35.0, min_silence: float = 0.5, pad: float = 0.08,
                     silences: Optional[Sequence[Tuple[float, float]]] = None) -> Proposal:
    """Konuşma aralarındaki sessizlikleri keser; her kesimin kenarında ``pad`` sn nefes payı bırakır."""
    from ...core.i18n import t as _t
    _tr, c = _clip(tl, clip_id)
    info = probe(c.src)
    if not info.has_audio:
        raise smart("no_source")
    sil = list(silences) if silences is not None else detect_silences(c.src, noise_db=noise_db, min_silence=min_silence,
                                                                       start=c.in_, duration=c.out - c.in_)
    cuts = [(a + pad, (b if b != float("inf") else c.out) - pad) for a, b in sil if (min(b, c.out) - a) > 2 * pad + 0.05]
    cuts = [(a, b) for a, b in cuts if b - a > 0.05]
    saved = sum(min(b, c.out) - max(a, c.in_) for a, b in cuts) / max(0.1, c.speed)
    if not cuts:
        return Proposal("silence", _t("editor.auto.nothing"), lambda tl_: None, {"empty": True, "count": 0, "seconds": 0.0})
    return Proposal("silence", _t("editor.auto.silence.result", count=len(cuts), seconds=f"{saved:.1f}"),
                    cut_ranges_apply(clip_id, cuts), {"count": len(cuts), "seconds": saved, "cuts": cuts})


# ===========================================================================================
# 2) Metinle kurgu: dökümde seçilen aralıkları sil
# ===========================================================================================
def plan_text_edit(tl: Timeline, clip_id: str, deleted: Sequence[Tuple[float, float]]) -> Proposal:
    """``deleted``: **kaynak zamanında** silinecek döküm aralıkları (kullanıcı döküm satırlarını/kelimeleri seçip siler)."""
    from ...core.i18n import t as _t
    _tr, c = _clip(tl, clip_id)
    cuts = [(max(c.in_, a), min(c.out, b)) for a, b in deleted if b > a]
    if not cuts:
        return Proposal("text_edit", _t("editor.auto.nothing"), lambda tl_: None, {"empty": True})
    saved = sum(b - a for a, b in cuts) / max(0.1, c.speed)
    return Proposal("text_edit", _t("editor.auto.silence.result", count=len(cuts), seconds=f"{saved:.1f}"),
                    cut_ranges_apply(clip_id, cuts), {"count": len(cuts), "seconds": saved, "cuts": cuts})


# ===========================================================================================
# 3) Stüdyo sesi (Adım 6 mastering zinciri)
# ===========================================================================================
def extract_audio(src: str, dst: str, start: float = 0.0, duration: Optional[float] = None) -> str:
    ff = require_ffmpeg()
    cmd = [ff, "-v", "error", "-nostdin", "-y"] + (["-ss", f"{start:.3f}"] if start else []) + \
        (["-t", f"{duration:.3f}"] if duration else []) + ["-i", src, "-vn", "-c:a", "pcm_s24le", "-ar", "48000", dst]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise smart("render_failed", technical=r.stderr[-500:])
    return dst


def studio_voice(tl: Timeline, clip_id: str, out_dir: Path, *, mastering_fn: Optional[Callable[..., Any]] = None,
                 on_progress: Optional[Callable[[float], None]] = None,
                 is_cancelled: Optional[Callable[[], bool]] = None) -> Proposal:
    """Klibin sesini Adım 6'daki mastering zincirinden geçirir (yüksek geçiren + EQ + sıkıştırma + de-esser + −14 LUFS),
    ``.wav`` üretir; uygulayınca özgün klip sessize alınır ve işlenmiş ses yeni bir ses klibi olarak aynı yere konur."""
    from ...core.i18n import t as _t
    from ..recorder import mastering
    tr, c = _clip(tl, clip_id)
    info = probe(c.src)
    if not info.has_audio:
        raise smart("no_source")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / f"{Path(c.src).stem}_{c.id}_raw.wav"
    extract_audio(c.src, str(raw), c.in_, c.out - c.in_)
    run = mastering_fn or mastering.master_file
    try:
        res = run(raw, on_progress=on_progress, is_cancelled=is_cancelled)
    finally:
        try:
            raw.unlink()
        except OSError:
            pass
    wav = str(res.path)
    start, speed, vol = c.start, c.speed, c.volume_db

    def apply(t: Timeline) -> None:
        found = t.find_clip(clip_id)
        if found is None:
            raise ops.EditError("klip yok")
        tr_, cc = found
        if info.has_video:
            cc.data["audio_muted"] = True
        else:
            tr_.clips.remove(cc)
        voice = next((x for x in t.tracks if x.kind == "audio" and x.name.lower() in ("seslendirme", "voice", "ses")), None)
        voice = voice or next((x for x in t.tracks if x.kind == "audio"), None) or ops.add_track(t, "audio", "Seslendirme")
        voice.clips.append(Clip(new_id(), "media", wav, in_=0.0, out=res.duration_s or (c.out - c.in_) / max(0.1, speed),
                                start=start, volume_db=vol, name="Stüdyo sesi"))
    return Proposal("voice", _t("editor.auto.applied", name=_t("editor.auto.voice")), apply, {"wav": wav})


# ===========================================================================================
# 4) Otomatik ducking
# ===========================================================================================
def plan_ducking(tl: Timeline, music_track: str, voice_tracks: Sequence[str], *, threshold: float = 0.04, ratio: float = 8.0) -> Proposal:
    """Konuşma kanalları sesliyken müzik kanalını kısar (``sidechaincompress``)."""
    from ...core.i18n import t as _t
    mt = tl.track(music_track)
    if mt is None or not any(tl.track(v) for v in voice_tracks):
        raise smart("no_source")

    def apply(t: Timeline) -> None:
        tr = t.track(music_track)
        if tr is None:
            raise ops.EditError("kanal yok")
        tr.effects = [e for e in tr.effects if e.get("type") != "duck"]
        tr.effects.append({"type": "duck", "enabled": True, "params": {"keys": list(voice_tracks), "threshold": threshold,
                                                                      "ratio": ratio, "attack": 20, "release": 400}})
    return Proposal("ducking", _t("editor.auto.applied", name=_t("editor.auto.ducking")), apply, {"music": music_track, "voice": list(voice_tracks)})


# ===========================================================================================
# 5) Renk: canlandır + eşleştir + kroma
# ===========================================================================================
def plan_vitalize(tl: Timeline, clip_id: str, *, frame: Optional[np.ndarray] = None, info=None) -> Proposal:
    from ...core.i18n import t as _t
    _tr, c = _clip(tl, clip_id)
    if frame is None:
        mid = c.start + c.duration / 2
        frame = frames.extract_frame(tl, mid, size=(320, 180), info=info)
    p = color.vitalize_params(frame)

    def apply(t: Timeline) -> None:
        found = t.find_clip(clip_id)
        if found is None:
            raise ops.EditError("klip yok")
        found[1].effects = [e for e in found[1].effects if e.get("name") != "vitalize"]
        found[1].effects.append({"type": "eq", "name": "vitalize", "enabled": True, "params": p})
        found[1].effects.append({"type": "sharpen", "name": "vitalize", "enabled": True, "params": {"amount": 0.6}})
    return Proposal("vitalize", _t("editor.auto.applied", name=_t("editor.auto.vitalize")), apply, {"params": p})


def plan_color_match(tl: Timeline, clip_id: str, ref_clip_id: str, lut_dir: Path, *, strength: float = 1.0,
                     src_frame: Optional[np.ndarray] = None, ref_frame: Optional[np.ndarray] = None, info=None) -> Proposal:
    """``clip_id``'nin rengini ``ref_clip_id``'ninkine yaklaştıran ``.cube`` LUT'u üretir ve efekt olarak ekler."""
    from ...core.i18n import t as _t
    _a, ca = _clip(tl, clip_id)
    _b, cb = _clip(tl, ref_clip_id)
    src = src_frame if src_frame is not None else frames.extract_frame(tl, ca.start + ca.duration / 2, size=(320, 180), info=info)
    ref = ref_frame if ref_frame is not None else frames.extract_frame(tl, cb.start + cb.duration / 2, size=(320, 180), info=info)
    lut = color.match_lut(src, ref, 17, strength)
    Path(lut_dir).mkdir(parents=True, exist_ok=True)
    path = color.write_cube(str(Path(lut_dir) / f"match_{clip_id}_{ref_clip_id}.cube"), lut, "color-match")

    def apply(t: Timeline) -> None:
        found = t.find_clip(clip_id)
        if found is None:
            raise ops.EditError("klip yok")
        found[1].effects = [e for e in found[1].effects if e.get("name") != "colormatch"]
        found[1].effects.append({"type": "lut", "name": "colormatch", "enabled": True, "params": {"file": path}})
    return Proposal("colormatch", _t("editor.auto.applied", name=_t("editor.auto.colormatch")), apply, {"lut": path})


def plan_lut(tl: Timeline, clip_id: str, cube_path: str) -> Proposal:
    """Kullanıcının seçtiği ``.cube`` LUT'unu klibe ekler (geçersiz dosya → hata)."""
    from ...core.i18n import t as _t
    _clip(tl, clip_id)
    color.read_cube(cube_path)                                           # doğrula

    def apply(t: Timeline) -> None:
        found = t.find_clip(clip_id)
        if found is not None:
            found[1].effects.append({"type": "lut", "name": "lut", "enabled": True, "params": {"file": cube_path}})
    return Proposal("lut", _t("editor.auto.applied", name="LUT"), apply, {"lut": cube_path})


# ===========================================================================================
# 6) Ritme göre kes (librosa)
# ===========================================================================================
def detect_beats(path: str) -> Tuple[float, List[float]]:
    librosa = _optional("librosa", "opt_librosa", "librosa")
    y, sr = librosa.load(path, sr=22050, mono=True)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    times = [float(x) for x in librosa.frames_to_time(beats, sr=sr)]
    return float(np.atleast_1d(tempo)[0]), times


def plan_beat_sync(tl: Timeline, music_clip_id: str, video_clip_ids: Sequence[str], *, every: int = 2,
                   beats: Optional[Sequence[float]] = None) -> Proposal:
    """Seçili video kliplerini müziğin vuruşlarına göre keser: her parça ``every`` vuruş sürer. Tek klip seçiliyse o klip vuruşlarda
    bölünür; birden çok klip seçiliyse sırayla vuruş aralıklarına yerleşir (fazlası kırpılır)."""
    from ...core.i18n import t as _t
    _tr, m = _clip(tl, music_clip_id)
    if beats is None:
        _tempo, beats = detect_beats(m.src)
    bt = [m.start + (b - m.in_) / max(0.1, m.speed) for b in beats if m.in_ <= b <= m.out]
    bt = bt[::max(1, every)]
    if len(bt) < 2 or not video_clip_ids:
        return Proposal("beat", _t("editor.auto.nothing"), lambda t: None, {"empty": True})
    ids = list(video_clip_ids)

    def apply(t: Timeline) -> None:
        if len(ids) == 1:
            found = t.find_clip(ids[0])
            if found is None:
                return
            tr0, c0 = found
            for b in bt[1:]:
                for c in list(tr0.clips):
                    if c.src == c0.src and c.start + 0.05 < b < c.end - 0.05:
                        ops.split_clip(t, c.id, b)
                        break
            return
        for i, cid in enumerate(ids):
            found = t.find_clip(cid)
            if found is None or i + 1 >= len(bt):
                continue
            tr, c = found
            c.start = bt[i]
            want = bt[i + 1] - bt[i]
            if c.duration > want:
                c.out = c.in_ + want * max(0.1, c.speed)
    return Proposal("beat", _t("editor.auto.applied", name=_t("editor.auto.beat")), apply, {"beats": len(bt)})


# ===========================================================================================
# 7) Otomatik kadraj (OpenCV)
# ===========================================================================================
def track_subject(path: str, start: float, end: float, step: float = 0.5) -> List[Tuple[float, float]]:
    """``[(kaynak_zamanı, x_oranı 0..1)]``: Haar yüz sezgisiyle konunun yatay merkezi (yüz yoksa ekran merkezi)."""
    cv2 = _optional("cv2", "opt_opencv", "opencv-python-headless")
    cap = cv2.VideoCapture(path)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    out: List[Tuple[float, float]] = []
    t = start
    last = 0.5
    try:
        while t < end:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.2, 5)
            if len(faces):
                x, _y, w, _h = max(faces, key=lambda f: f[2] * f[3])
                last = float((x + w / 2) / frame.shape[1])
            out.append((t, last))
            t += step
    finally:
        cap.release()
    return out


def plan_reframe(tl: Timeline, clip_id: str, *, target: Tuple[int, int] = (1080, 1920),
                 track: Optional[Sequence[Tuple[float, float]]] = None) -> Proposal:
    """16:9 klibi dikey tuvale sığdırıp konuyu izleyen ``scale`` + ``x`` anahtar kareleri üretir; proje boyutunu hedefe çevirir."""
    from ...core.i18n import t as _t
    _tr, c = _clip(tl, clip_id)
    info = probe(c.src)
    if not info.has_video or not info.width:
        raise smart("no_source")
    pts = list(track) if track is not None else track_subject(c.src, c.in_, c.out)
    tw, th = target
    fit_h = tw * info.height / info.width                                # tuvale genişliğe göre sığan yükseklik
    zoom = max(1.0, th / fit_h)
    maxshift = (zoom - 1) / 2

    def apply(t: Timeline) -> None:
        found = t.find_clip(clip_id)
        if found is None:
            raise ops.EditError("klip yok")
        cc = found[1]
        t.width, t.height = tw, th
        cc.keyframes["scale"] = [Keyframe(0.0, round(zoom, 4))]
        kfs: List[Keyframe] = []
        for ts, xr in pts:
            lt = max(0.0, (ts - cc.in_) / max(0.1, cc.speed))
            dx = max(-maxshift, min(maxshift, (0.5 - xr) * zoom))
            kfs.append(Keyframe(round(lt, 3), round(dx, 4), "ease_in_out"))
        cc.keyframes["x"] = kfs or [Keyframe(0.0, 0.0)]
    return Proposal("reframe", _t("editor.auto.applied", name=_t("editor.auto.reframe")), apply, {"points": len(pts), "zoom": zoom})


# ===========================================================================================
# 8) Yapay zekâ ile yeşil perde (rembg)
# ===========================================================================================
def remove_background(src: str, dst: str, *, start: float = 0.0, duration: Optional[float] = None, max_size: int = 720,
                      report: Optional[Callable[[float, str], None]] = None, cancelled: Optional[Callable[[], bool]] = None) -> str:
    """Kareleri rembg'den geçirip alfa kanallı ProRes 4444 ``.mov`` üretir (ses taşınmaz)."""
    rembg = _optional("rembg", "opt_rembg", "rembg onnxruntime")
    from PIL import Image
    info = probe(src)
    scale = min(1.0, max_size / max(1, info.height))
    w, h = int(info.width * scale) // 2 * 2, int(info.height * scale) // 2 * 2
    fps = info.fps or 30.0
    total = max(1, int(((duration or info.duration) * fps)))
    ff = require_ffmpeg()
    dec = subprocess.Popen([ff, "-v", "error", "-nostdin"] + (["-ss", f"{start:.3f}"] if start else []) +
                           (["-t", f"{duration:.3f}"] if duration else []) +
                           ["-i", src, "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen([ff, "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
                            "-r", fps_arg(fps), "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                            "-f", "mov", dst + ".part"], stdin=subprocess.PIPE)
    n = 0
    try:
        while True:
            if cancelled and cancelled():
                raise RuntimeError("cancelled")
            raw = dec.stdout.read(w * h * 3)                              # type: ignore[union-attr]
            if len(raw) < w * h * 3:
                break
            img = Image.frombytes("RGB", (w, h), raw)
            out = rembg.remove(img)
            enc.stdin.write(out.convert("RGBA").tobytes())                # type: ignore[union-attr]
            n += 1
            if report:
                report(min(1.0, n / total), f"{n}/{total}")
        enc.stdin.close()                                                 # type: ignore[union-attr]
        enc.wait()
        dec.wait()
        if enc.returncode != 0:
            raise smart("render_failed")
        os.replace(dst + ".part", dst)
    except BaseException:
        for p in (dec, enc):
            if p.poll() is None:
                p.kill()
        try:
            os.unlink(dst + ".part")
        except OSError:
            pass
        raise
    return dst


def plan_green_screen(tl: Timeline, clip_id: str, out_dir: Path, **kw: Any) -> Proposal:
    from ...core.i18n import t as _t
    _tr, c = _clip(tl, clip_id)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    dst = str(Path(out_dir) / f"{Path(c.src).stem}_{c.id}_cutout.mov")
    remove_background(c.src, dst, start=c.in_, duration=c.out - c.in_, **kw)

    def apply(t: Timeline) -> None:
        found = t.find_clip(clip_id)
        if found is None:
            raise ops.EditError("klip yok")
        cc = found[1]
        cc.data["original_src"], cc.data["original_in"], cc.data["original_out"] = cc.src, cc.in_, cc.out
        cc.src, cc.in_, cc.out = dst, 0.0, c.out - c.in_
    return Proposal("greenscreen", _t("editor.auto.applied", name=_t("editor.auto.greenscreen")), apply, {"cutout": dst})


def plan_chroma_key(tl: Timeline, clip_id: str, color_hex: str = "0x00ff00", similarity: float = 0.15, blend: float = 0.1) -> Proposal:
    """Klasik (yapay zekâsız) yeşil perde: ``chromakey`` efekti. Ağır paket gerektirmez."""
    from ...core.i18n import t as _t
    _clip(tl, clip_id)

    def apply(t: Timeline) -> None:
        found = t.find_clip(clip_id)
        if found is not None:
            found[1].effects.append({"type": "chromakey", "enabled": True,
                                     "params": {"color": color_hex, "similarity": similarity, "blend": blend}})
    return Proposal("greenscreen", _t("editor.auto.applied", name=_t("editor.auto.greenscreen")), apply, {})


# ===========================================================================================
# 9) Dinamik altyazı
# ===========================================================================================
def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def build_ass(segments: Sequence[Any], width: int, height: int, *, font: Optional[str] = None, size_frac: float = 0.055,
              primary: str = "&H00FFFFFF", highlight: str = "&H0000E0FF", offset: float = 0.0) -> str:
    """Kelime kelime vurgulanan (karaoke ``\\k``) ASS altyazı üretir. ``segments``: ``start/end/text`` nitelikli nesneler."""
    font = font or subtitle_font()              # Android'de fontconfig yok: uygulamanın yazı tipi
    size = max(12, int(height * size_frac))
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 0\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
            "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Dyn,%s,%d,%s,%s,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,%d,1,2,40,40,%d,1\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n") % \
        (width, height, font, size, highlight, primary, max(2, size // 12), int(height * 0.08))
    lines = []
    for seg in segments:
        words = str(seg.text).split()
        if not words:
            continue
        dur = max(0.05, seg.end - seg.start)
        weights = [max(1, len(w)) for w in words]
        tot = sum(weights)
        body = "".join(f"{{\\k{max(1, int(round(dur * 100 * wt / tot)))}}}{w} " for w, wt in zip(words, weights)).strip()
        lines.append(f"Dialogue: 0,{_ass_time(seg.start + offset)},{_ass_time(seg.end + offset)},Dyn,,0,0,0,,{body}")
    return head + "\n".join(lines) + "\n"


def plan_dynamic_subtitles(tl: Timeline, clip_id: str, segments: Sequence[Any], out_dir: Path) -> Proposal:
    """Dökümü (``Segment`` listesi, **klip yerel zamanı**) kelime vurgulu altyazıya çevirip ``sub`` kanalına ekler."""
    from ...core.i18n import t as _t
    _tr, c = _clip(tl, clip_id)
    if not segments:
        return Proposal("subtitles", _t("editor.auto.nothing"), lambda t: None, {"empty": True})
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    ass_path = Path(out_dir) / f"subs_{clip_id}.ass"
    ass_path.write_text(build_ass(segments, tl.width, tl.height, offset=c.start), encoding="utf-8")
    start, dur = c.start, c.duration

    def apply(t: Timeline) -> None:
        sub = next((x for x in t.tracks if x.kind == "sub"), None) or ops.add_track(t, "sub", "Altyazı")
        sub.clips = [x for x in sub.clips if x.data.get("ass") != str(ass_path)]
        sub.clips.append(Clip(new_id(), "text", start=start, duration_hint=dur, name="Altyazı", data={"ass": str(ass_path)}))
    return Proposal("subtitles", _t("editor.auto.applied", name=_t("editor.auto.subtitles")), apply, {"ass": str(ass_path), "cues": len(segments)})


# ===========================================================================================
# 10) Filigran
# ===========================================================================================
CORNERS = ("tl", "tr", "bl", "br")


def plan_watermark(tl: Timeline, image_path: str, *, corner: str = "br", width_frac: float = 0.15, opacity: float = 0.75,
                   margin: float = 0.03, image_size: Optional[Tuple[int, int]] = None) -> Proposal:
    """Logoyu seçilen köşeye, tüm süre boyunca, yarı saydam yerleştirir (en üst video kanalı olarak)."""
    from ...core.i18n import t as _t
    if corner not in CORNERS:
        raise ops.EditError(f"geçersiz köşe: {corner}")
    iw, ih = image_size or (lambda i: (i.width, i.height))(probe(image_path))
    if not iw or not ih:
        raise smart("probe_failed", name=Path(image_path).name)
    W, H = tl.width, tl.height
    fit = min(W / iw, H / ih)
    w0 = iw * fit
    want_w = width_frac * W
    scale = want_w / w0
    want_h = want_w * ih / iw
    x = (0.5 - width_frac / 2 - margin) * (1 if corner[1] == "r" else -1)
    y = (0.5 - want_h / (2 * H) - margin * W / H) * (1 if corner[0] == "b" else -1)
    dur = max(0.1, tl.duration)

    def apply(t: Timeline) -> None:
        tr = ops.add_track(t, "video", "Filigran")
        clip = Clip(new_id(), "image", image_path, in_=0.0, out=dur, start=0.0, duration_hint=dur, name="Filigran")
        clip.keyframes = {"scale": [Keyframe(0, round(scale, 4))], "x": [Keyframe(0, round(x, 4))], "y": [Keyframe(0, round(y, 4))],
                          "opacity": [Keyframe(0, opacity)]}
        tr.clips.append(clip)
    return Proposal("watermark", _t("editor.auto.applied", name=_t("editor.auto.watermark")), apply, {"corner": corner})


# ===========================================================================================
# 11) Kareyi dondur
# ===========================================================================================
def plan_freeze(tl: Timeline, clip_id: str, t_at: float, seconds: float = 2.0) -> Proposal:
    from ...core.i18n import t as _t
    _clip(tl, clip_id)
    return Proposal("freeze", _t("editor.auto.applied", name=_t("editor.auto.freeze")),
                    lambda t: ops.freeze_frame(t, clip_id, t_at, seconds), {"at": t_at})


# ===========================================================================================
# 12) Ekran / kamera kaydı
# ===========================================================================================
def recording_command(kind: str, out: str, *, fps: int = 30, size: str = "1920x1080", source: Optional[List[str]] = None,
                      ffmpeg: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> List[str]:
    """``kind``: ``"screen"`` | ``"webcam"``. ``source``: test/özel giriş argümanları (``-f lavfi -i testsrc`` gibi)."""
    ff = ffmpeg or require_ffmpeg()
    env = env if env is not None else dict(os.environ)
    if source is None:
        if kind == "webcam":
            if sys.platform == "win32":
                raise smart("no_source")                       # DirectShow aygıt adı gerekir; henüz desteklenmiyor
            dev = next((str(p) for p in sorted(Path("/dev").glob("video*"))), None)
            if dev is None:
                raise smart("no_source")
            source = ["-f", "v4l2", "-framerate", str(fps), "-i", dev]
        elif sys.platform == "win32":
            source = ["-f", "gdigrab", "-framerate", str(fps), "-i", "desktop"]      # tüm masaüstü; -video_size gerekmez
        else:
            display = env.get("DISPLAY")
            if not display:
                raise smart("no_source")
            source = ["-f", "x11grab", "-framerate", str(fps), "-video_size", size, "-i", display]
    return [ff, "-hide_banner", "-y", "-v", "error", *source, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+frag_keyframe+empty_moov", "-f", "mp4", out]


class Recording:
    """Çalışan kayıt: ``stop()`` FFmpeg'e ``q`` gönderir ve dosyayı kapatır (parçalı mp4 olduğundan yarım kalsa da oynatılır)."""

    def __init__(self, cmd: List[str], out: str) -> None:
        self.out = out
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self, timeout: float = 15.0) -> str:
        if self.running:
            try:
                self.proc.stdin.write(b"q")                                # type: ignore[union-attr]
                self.proc.stdin.flush()                                    # type: ignore[union-attr]
            except OSError:
                pass
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(timeout=5)
        if not Path(self.out).exists() or Path(self.out).stat().st_size == 0:
            err = (self.proc.stderr.read().decode("utf-8", "replace") if self.proc.stderr else "")[-500:]
            raise smart("render_failed", technical=err)
        return self.out


def start_recording(kind: str, out: str, **kw: Any) -> Recording:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    return Recording(recording_command(kind, out, **kw), out)
