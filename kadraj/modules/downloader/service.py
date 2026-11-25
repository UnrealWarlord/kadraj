"""Medya İndirici servisi: yt-dlp Python API'si, taslak durumu ve iş kuyruğu köprüsü.

Durum burada tutulur (taslak, son bilgi, son işler); sayfalar yalnızca görünümdür, bu
yüzden Basit ↔ Legend geçişi indirme durumunu kaybettirmez.

* ``probe`` — bağlantıyı çözümler (arka plan iş parçacığı). Sonuç ``probed`` / ``probeFailed``
  sinyalleriyle ana iş parçacığına döner.
* ``submit`` — ``"download"`` işi açar; işleyici ``ctx.jobs`` içinde çalışır.
* ``yt_dlp`` ilk kullanımda içe aktarılır; bu modülün içe aktarımı ağır bağımlılık yüklemez.
  Testler ``ydl_factory`` ile sahte bir ``YoutubeDL`` verir; ağ kullanılmaz.

Saf yardımcılar (biçim seçici, yt-dlp seçenekleri, zaman kodu, boyut tahmini) modül
düzeyindedir ve doğrudan test edilir.
"""
from __future__ import annotations

import math
import re
import shlex
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, Signal

from ...core import i18n
from ...core.contracts import SIMPLE_SAMPLE_RATE, Job, SmartError, Topics
from ...core.errors import translate
from ...storage import disk, folders
from .strings import K

JOB_KIND = "download"

MODE_AV = "av"            # Video + Ses
MODE_AUDIO = "audio"      # Sadece Ses → .wav
MODE_VIDEO = "video"      # Sadece Görüntü
MODES: Tuple[str, ...] = (MODE_AV, MODE_AUDIO, MODE_VIDEO)

QUALITY_FAST = "fast"            # 720p
QUALITY_BALANCED = "balanced"    # 1080p
QUALITY_BEST = "best"            # 4K'ya kadar
QUALITIES: Tuple[str, ...] = (QUALITY_FAST, QUALITY_BALANCED, QUALITY_BEST)
QUALITY_HEIGHT: Dict[str, int] = {QUALITY_FAST: 720, QUALITY_BALANCED: 1080, QUALITY_BEST: 2160}

DEFAULT_SUB_LANGS: Tuple[str, ...] = ("tr",)
SOCKET_TIMEOUT = 20                       # saniye; ağ yavaşsa takılmamak için
WAV_BYTES_PER_SECOND = SIMPLE_SAMPLE_RATE * 2 * 3   # 48 kHz, stereo, 24-bit

_URL_RE = re.compile(r"^https?://[^\s/]+\.[^\s]+$", re.IGNORECASE)
_RATE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kKmMgG]?)\s*$")
_RATE_UNITS = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}


class _Cancelled(Exception):
    """İlerleme kancası iptali fark edince yükseltilir; işleyici bunu sessizce yakalar."""


# ---------------------------------------------------------------------------
# Zaman ve boyut yardımcıları
# ---------------------------------------------------------------------------
def parse_timecode(text: Any) -> float:
    """``"1:20"``, ``"01:02:03"``, ``"80"``, ``"1:20.5"`` → saniye. Geçersizse ``ValueError``."""
    raw = str(text or "").strip().replace(",", ".")
    if not raw:
        raise ValueError("boş zaman kodu")
    parts = raw.split(":")
    if len(parts) > 3:
        raise ValueError(raw)
    values = [float(p) for p in parts]          # geçersiz parça ValueError verir
    if not all(math.isfinite(v) and v >= 0 for v in values):
        raise ValueError(raw)
    if len(values) > 1 and any(v >= 60 for v in values[1:]):
        raise ValueError(raw)
    seconds = 0.0
    for v in values:
        seconds = seconds * 60 + v
    return seconds


def format_timecode(seconds: float, *, full: bool = False) -> str:
    """``80`` → ``1:20``; ``full=True`` veya bir saatten uzunsa ``00:01:20``."""
    total = int(round(max(float(seconds or 0.0), 0.0)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if full or h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def describe_length(seconds: float) -> str:
    """``145`` → ``2 dk 25 sn``; Basit Mod'da süre dili."""
    total = int(round(max(float(seconds or 0.0), 0.0)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    parts: List[str] = []
    if h:
        parts.append(f"{h} sa")
    if m:
        parts.append(f"{m} dk")
    if s or not parts:
        parts.append(f"{s} sn")
    return " ".join(parts)


def parse_rate(text: Any) -> Optional[int]:
    """``"5M"`` → bayt/saniye (5·1024²). Boş ya da geçersizse ``None`` (sınırsız)."""
    m = _RATE_RE.match(str(text or ""))
    if not m:
        return None
    value = float(m.group(1)) * _RATE_UNITS[m.group(2).lower()]
    return int(value) if value > 0 else None


def is_probable_url(url: str) -> bool:
    return bool(_URL_RE.match(str(url or "").strip()))


# ---------------------------------------------------------------------------
# Veri tipleri
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Options:
    """Legend seçenek kutuları (``--write-subs``, ``--embed-thumbnail``, ``--yes-playlist``, ``--limit-rate``)."""
    subtitles: bool = False
    sub_langs: Tuple[str, ...] = DEFAULT_SUB_LANGS
    thumbnail: bool = False
    embed_thumbnail: bool = False
    playlist: bool = False
    rate_limit: str = ""                       # "5M"; boşsa sınırsız


@dataclass(frozen=True)
class DownloadRequest:
    """Tek bir indirme isteği (iş yükü). ``end=None`` → sona kadar."""
    url: str
    mode: str = MODE_AV
    quality: str = QUALITY_BALANCED
    format_ids: Tuple[str, ...] = ()           # Legend'de elle seçilen biçimler; boşsa mode + quality
    start: float = 0.0
    end: Optional[float] = None
    options: Options = Options()
    output_dir: str = ""

    def to_payload(self) -> Dict[str, Any]:
        o = self.options
        return {
            "url": self.url,
            "mode": self.mode,
            "quality": self.quality,
            "format_ids": list(self.format_ids),
            "start": float(self.start),
            "end": None if self.end is None else float(self.end),
            "options": {
                "subtitles": bool(o.subtitles), "sub_langs": list(o.sub_langs),
                "thumbnail": bool(o.thumbnail), "embed_thumbnail": bool(o.embed_thumbnail),
                "playlist": bool(o.playlist), "rate_limit": str(o.rate_limit or ""),
            },
            "output_dir": self.output_dir,
        }

    @classmethod
    def from_payload(cls, data: Mapping[str, Any]) -> "DownloadRequest":
        raw_opts = data.get("options") if isinstance(data.get("options"), Mapping) else {}
        options = Options(
            subtitles=bool(raw_opts.get("subtitles")),
            sub_langs=tuple(str(x) for x in (raw_opts.get("sub_langs") or DEFAULT_SUB_LANGS)),
            thumbnail=bool(raw_opts.get("thumbnail")),
            embed_thumbnail=bool(raw_opts.get("embed_thumbnail")),
            playlist=bool(raw_opts.get("playlist")),
            rate_limit=str(raw_opts.get("rate_limit") or ""),
        )
        end = data.get("end")
        mode = str(data.get("mode") or MODE_AV)
        quality = str(data.get("quality") or QUALITY_BALANCED)
        return cls(
            url=str(data.get("url") or ""),
            mode=mode if mode in MODES else MODE_AV,
            quality=quality if quality in QUALITIES else QUALITY_BALANCED,
            format_ids=tuple(str(x) for x in (data.get("format_ids") or ())),
            start=float(data.get("start") or 0.0),
            end=None if end is None else float(end),
            options=options,
            output_dir=str(data.get("output_dir") or ""),
        )


@dataclass(frozen=True)
class FormatRow:
    id: str
    ext: str
    resolution: str
    height: int
    fps: float
    vcodec: str
    acodec: str
    size: Optional[int]
    tbr: float
    abr: float
    note: str

    @property
    def has_video(self) -> bool:
        return self.vcodec != "none"

    @property
    def has_audio(self) -> bool:
        return self.acodec != "none"


@dataclass(frozen=True)
class EntryRef:
    url: str
    title: str


@dataclass(frozen=True)
class VideoInfo:
    url: str
    title: str
    extractor: str
    duration: float
    max_height: int
    subtitles: Tuple[str, ...]
    is_playlist: bool
    entries: Tuple[EntryRef, ...]
    formats: Tuple[FormatRow, ...]

    @property
    def is_youtube(self) -> bool:
        return "youtube" in self.extractor.lower()

    @property
    def site_name(self) -> str:
        return "YouTube" if self.is_youtube else (self.extractor or "Web")


@dataclass(frozen=True)
class Draft:
    """Sayfalar arasında korunan indirme taslağı (durum servistedir)."""
    url: str = ""
    mode: str = MODE_AV
    quality: str = QUALITY_BALANCED
    format_ids: Tuple[str, ...] = ()
    partial: bool = False
    start: float = 0.0
    end: Optional[float] = None
    options: Options = Options()


# ---------------------------------------------------------------------------
# yt-dlp seçenekleri (saf)
# ---------------------------------------------------------------------------
def format_selector(mode: str, quality: str, format_ids: Sequence[str] = ()) -> str:
    if format_ids:
        return "+".join(format_ids)
    if mode == MODE_AUDIO:
        return "bestaudio/best"
    cap = QUALITY_HEIGHT.get(quality, QUALITY_HEIGHT[QUALITY_BALANCED])
    if mode == MODE_VIDEO:
        return f"bestvideo[height<={cap}]/bestvideo"
    return f"bestvideo[height<={cap}]+bestaudio/best[height<={cap}]/best"


def probe_options(*, playlist: bool = False) -> Dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": not playlist,
        "extract_flat": "in_playlist",
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": 1,
    }


def build_ydl_options(req: DownloadRequest, *, out_dir: Path,
                      progress_hook: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """İndirme isteğini yt-dlp seçeneklerine çevirir. Ağ ya da ffmpeg çağrısı yapmaz."""
    opts: Dict[str, Any] = {
        "format": format_selector(req.mode, req.quality, req.format_ids),
        "outtmpl": str(Path(out_dir) / "%(title).150B [%(id)s].%(ext)s"),
        "noplaylist": not req.options.playlist,
        "restrictfilenames": False,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": 2,
        "fragment_retries": 2,
        "postprocessors": [],
        "postprocessor_args": {},
        "progress_hooks": [progress_hook] if progress_hook else [],
    }
    if req.mode == MODE_AV:
        opts["merge_output_format"] = "mp4"
    if req.mode == MODE_AUDIO:
        # Ses çıktısı yalnızca WAV; Basit sabit 48 kHz / 24-bit (plan §0.4).
        opts["postprocessors"].append({"key": "FFmpegExtractAudio", "preferredcodec": "wav"})
        opts["postprocessor_args"]["ExtractAudio"] = ["-ar", str(SIMPLE_SAMPLE_RATE), "-c:a", "pcm_s24le"]
    o = req.options
    if o.subtitles:
        opts["writesubtitles"] = True
        opts["subtitleslangs"] = list(o.sub_langs or DEFAULT_SUB_LANGS)
        opts["subtitlesformat"] = "srt/best"
        opts["postprocessors"].append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
    embed = o.embed_thumbnail and req.mode != MODE_AUDIO
    if o.thumbnail or embed:
        opts["writethumbnail"] = True
    if embed:
        opts["postprocessors"].append({"key": "EmbedThumbnail"})
    rate = parse_rate(o.rate_limit)
    if rate:
        opts["ratelimit"] = rate
    if req.start > 0 or req.end is not None:
        from yt_dlp.utils import download_range_func   # tembel: yalnızca kırpmada
        end = math.inf if req.end is None else float(req.end)
        opts["download_ranges"] = download_range_func(None, [(float(req.start), end)])
        opts["force_keyframes_at_cuts"] = True
    return opts


def command_preview(req: DownloadRequest) -> str:
    """Legend'de gösterilen eşdeğer yt-dlp komutu (yalnızca metin; çalıştırılmaz)."""
    parts = ["yt-dlp", "-f", shlex.quote(format_selector(req.mode, req.quality, req.format_ids))]
    if req.start > 0 or req.end is not None:
        end = "inf" if req.end is None else format_timecode(req.end, full=True)
        parts += ["--download-sections", shlex.quote(f"*{format_timecode(req.start, full=True)}-{end}")]
    if req.mode == MODE_AUDIO:
        parts += ["--extract-audio", "--audio-format", "wav"]
    elif req.mode == MODE_AV:
        parts += ["--merge-output-format", "mp4"]
    o = req.options
    if o.subtitles:
        parts += ["--write-subs", "--sub-langs", ",".join(o.sub_langs or DEFAULT_SUB_LANGS)]
    if o.embed_thumbnail and req.mode != MODE_AUDIO:
        parts.append("--embed-thumbnail")
    elif o.thumbnail:
        parts.append("--write-thumbnail")
    if o.playlist:
        parts.append("--yes-playlist")
    if o.rate_limit:
        parts += ["--limit-rate", o.rate_limit]
    parts.append(shlex.quote(req.url))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Bilgi çözümleme (saf)
# ---------------------------------------------------------------------------
def _num(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _format_row(f: Mapping[str, Any]) -> FormatRow:
    height = int(_num(f.get("height")))
    vcodec = str(f.get("vcodec") or "none")
    acodec = str(f.get("acodec") or "none")
    has_video = vcodec != "none"
    raw_size = f.get("filesize") or f.get("filesize_approx")
    size = int(raw_size) if isinstance(raw_size, (int, float)) and raw_size > 0 else None
    if height:
        resolution = f"{height}p"
    elif not has_video:
        resolution = "—"
    else:
        resolution = str(f.get("resolution") or "—")
    return FormatRow(
        id=str(f.get("format_id") or ""),
        ext=str(f.get("ext") or ""),
        resolution=resolution,
        height=height,
        fps=_num(f.get("fps")),
        vcodec=vcodec,
        acodec=acodec,
        size=size,
        tbr=_num(f.get("tbr")),
        abr=_num(f.get("abr")),
        note=str(f.get("format_note") or ""),
    )


def _entry_url(e: Mapping[str, Any]) -> str:
    for key in ("webpage_url", "url"):
        value = str(e.get(key) or "")
        if value.startswith("http"):
            return value
    ident = str(e.get("id") or "")
    if ident and "youtube" in str(e.get("ie_key") or e.get("extractor") or "").lower():
        return f"https://www.youtube.com/watch?v={ident}"
    return ""


def info_from_raw(raw: Mapping[str, Any], url: str) -> VideoInfo:
    """yt-dlp ``extract_info`` sözlüğünü ``VideoInfo``'ya çevirir. Eksik alanlar tolere edilir."""
    if not isinstance(raw, Mapping):
        raise ValueError("bağlantıdan bilgi okunamadı")
    kind = str(raw.get("_type") or "video")
    extractor = str(raw.get("extractor") or raw.get("extractor_key") or "")
    if kind in ("playlist", "multi_video") or ("entries" in raw and "formats" not in raw):
        entries: List[EntryRef] = []
        for e in raw.get("entries") or []:
            if not isinstance(e, Mapping):
                continue
            entry_url = _entry_url(e)
            if entry_url:
                entries.append(EntryRef(url=entry_url, title=str(e.get("title") or "")))
        return VideoInfo(url=url, title=str(raw.get("title") or ""), extractor=extractor, duration=0.0,
                         max_height=0, subtitles=(), is_playlist=True, entries=tuple(entries), formats=())
    formats = tuple(_format_row(f) for f in (raw.get("formats") or []) if isinstance(f, Mapping))
    max_height = max((f.height for f in formats), default=int(_num(raw.get("height"))))
    subs = raw.get("subtitles")
    langs = tuple(sorted(str(k) for k in subs.keys())) if isinstance(subs, Mapping) else ()
    return VideoInfo(url=url, title=str(raw.get("title") or ""), extractor=extractor,
                     duration=_num(raw.get("duration")), max_height=int(max_height), subtitles=langs,
                     is_playlist=False, entries=(), formats=formats)


def _best(items: Sequence[FormatRow], pred: Callable[[FormatRow], bool],
          key: Callable[[FormatRow], Tuple[float, ...]]) -> Optional[FormatRow]:
    picked = [f for f in items if pred(f)]
    return max(picked, key=key) if picked else None


def _format_bytes(f: FormatRow, duration: float) -> Optional[int]:
    if f.size:
        return f.size
    if f.tbr > 0 and duration > 0:
        return int(f.tbr * 1000 / 8 * duration)
    return None


def estimate_bytes(info: VideoInfo, mode: str, quality: str, start: float = 0.0,
                   end: Optional[float] = None) -> Optional[int]:
    """Kalite ve aralığa göre yaklaşık boyut (bayt). Bilinmiyorsa ``None``."""
    if info.is_playlist or info.duration <= 0:
        return None
    length = max((info.duration if end is None else min(end, info.duration)) - start, 0.0)
    if length <= 0:
        return None
    if mode == MODE_AUDIO:
        return int(length * WAV_BYTES_PER_SECOND)
    cap = QUALITY_HEIGHT.get(quality, QUALITY_HEIGHT[QUALITY_BALANCED])
    fmts = info.formats
    video = _best(fmts, lambda f: f.has_video and not f.has_audio and 0 < f.height <= cap,
                  lambda f: (float(f.height), f.tbr))
    audio = _best(fmts, lambda f: f.has_audio and not f.has_video, lambda f: (f.abr or f.tbr, 0.0))
    if mode == MODE_VIDEO:
        picks = [video]
    else:
        combined = _best(fmts, lambda f: f.has_video and f.has_audio and 0 < f.height <= cap,
                         lambda f: (float(f.height), f.tbr))
        picks = [video, audio] if video is not None and audio is not None else [combined]
    total = 0.0
    for f in picks:
        if f is None:
            return None
        size = _format_bytes(f, info.duration)
        if size is None:
            return None
        total += size
    return int(total * (length / info.duration))


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------
def _tool_for(exc: BaseException) -> str:
    return "ffmpeg" if "ffmpeg" in str(exc).lower() else "yt-dlp"


def _collect_files(info: Mapping[str, Any], ydl: Any, mode: str) -> List[str]:
    entries = info.get("entries")
    items = [e for e in entries if isinstance(e, Mapping)] if entries else [info]
    files: List[str] = []
    for item in items:
        path = _final_path(item, ydl, mode)
        if path:
            files.append(path)
    return files


def _final_path(item: Mapping[str, Any], ydl: Any, mode: str) -> str:
    candidates = [str(item.get("filepath") or "")]
    for rd in item.get("requested_downloads") or []:
        if isinstance(rd, Mapping):
            candidates.append(str(rd.get("filepath") or ""))
    for c in candidates:
        if c and Path(c).exists():
            return c
    try:
        guess = str(ydl.prepare_filename(dict(item)))
    except Exception:  # noqa: BLE001 — yol tahmini yardımcı; başarısızsa boş döner
        guess = ""
    if guess and mode == MODE_AUDIO:
        guess = str(Path(guess).with_suffix(".wav"))
    if guess and Path(guess).exists():
        return guess
    return next((c for c in candidates if c), "")


class DownloaderService(QObject):
    """``ctx.service("downloader")``. Sinyaller ana iş parçacığında alınır."""

    name = "downloader"
    probeStarted = Signal()
    probed = Signal(object)          # VideoInfo
    probeFailed = Signal(object)     # SmartError
    draftChanged = Signal()
    queueChanged = Signal()

    #: Testler için sahte ``YoutubeDL`` fabrikası. ``None`` → ``yt_dlp.YoutubeDL``.
    ydl_factory: Optional[Callable[[Dict[str, Any]], Any]] = None

    def __init__(self) -> None:
        super().__init__()
        self.ctx: Any = None
        self.draft = Draft()
        self.info: Optional[VideoInfo] = None
        self._token = 0
        self._unsubs: List[Callable[[], None]] = []

    # -- yaşam döngüsü -----------------------------------------------------
    def start(self, ctx: Any) -> None:
        self.ctx = ctx
        ctx.jobs.register_handler(JOB_KIND, self._handle_download)
        self._unsubs.append(ctx.bus.subscribe(Topics.JOB_ADDED, lambda _j: self.queueChanged.emit()))
        self._unsubs.append(ctx.bus.subscribe(Topics.JOB_UPDATED, lambda _j: self.queueChanged.emit()))

    def stop(self) -> None:
        for unsubscribe in self._unsubs:
            unsubscribe()
        self._unsubs.clear()
        self.ctx = None

    # -- taslak ------------------------------------------------------------
    def _changed(self) -> None:
        self.draftChanged.emit()

    def _invalidate(self) -> None:
        self._token += 1                 # bekleyen bir çözümleme artık geçerli değil
        self.info = None

    def set_url(self, url: str) -> None:
        self.draft = replace(self.draft, url=str(url or "").strip())
        self._invalidate()
        self._changed()

    def set_mode(self, mode: str) -> None:
        if mode in MODES:
            self.draft = replace(self.draft, mode=mode)
            self._changed()

    def set_quality(self, quality: str) -> None:
        if quality in QUALITIES:
            self.draft = replace(self.draft, quality=quality)
            self._changed()

    def set_format_ids(self, ids: Sequence[str]) -> None:
        self.draft = replace(self.draft, format_ids=tuple(str(i) for i in ids))
        self._changed()

    def set_range(self, start: float, end: Optional[float], *, partial: Optional[bool] = None) -> None:
        self.draft = replace(self.draft, start=max(float(start), 0.0),
                             end=None if end is None else float(end),
                             partial=self.draft.partial if partial is None else bool(partial))
        self._changed()

    def set_partial(self, partial: bool) -> None:
        self.draft = replace(self.draft, partial=bool(partial))
        self._changed()

    def set_options(self, options: Options) -> None:
        self.draft = replace(self.draft, options=options)
        self._changed()

    # -- çözümleme ---------------------------------------------------------
    def probe(self, url: Optional[str] = None) -> None:
        """Bağlantıyı arka planda çözümler. Sonuç ``probed`` ya da ``probeFailed`` ile gelir."""
        if url is not None:
            self.draft = replace(self.draft, url=str(url).strip())
        self._invalidate()
        target = self.draft.url
        self._changed()
        if not is_probable_url(target):
            self.probeFailed.emit(SmartError(
                "input.url_invalid", i18n.t(K.URL_INVALID_TITLE), i18n.t(K.URL_INVALID_SUMMARY),
                steps=(i18n.t(K.URL_INVALID_STEP),), source="downloader"))
            return
        token = self._token
        playlist = self.draft.options.playlist
        self.probeStarted.emit()
        threading.Thread(target=self._probe_worker, args=(target, playlist, token),
                         name="kadraj-probe", daemon=True).start()

    def _probe_worker(self, url: str, playlist: bool, token: int) -> None:
        try:
            info = self.fetch_info(url, playlist=playlist)
        except Exception as exc:  # noqa: BLE001 — ağ/yt-dlp hatası asla çökertmez
            if token == self._token:
                self.probeFailed.emit(self.translate_error(exc, url))
            return
        if token == self._token:
            self.info = info
            self.probed.emit(info)

    def _ydl(self, opts: Dict[str, Any]) -> Any:
        if self.ydl_factory is not None:
            return self.ydl_factory(opts)
        from yt_dlp import YoutubeDL          # tembel içe aktarma
        return YoutubeDL(opts)

    def fetch_info(self, url: str, *, playlist: bool = False) -> VideoInfo:
        """Engelleyici çağrı (iş parçacığında çalıştırılır). Ağ yoksa istisna atar."""
        with self._ydl(probe_options(playlist=playlist)) as ydl:
            raw = ydl.extract_info(url, download=False)
        if raw is None:
            raise ValueError("bağlantıdan bilgi alınamadı")
        return info_from_raw(raw, url)

    def translate_error(self, exc: BaseException, url: str = "") -> SmartError:
        return translate(exc, {"tool": _tool_for(exc), "stderr": str(exc), "url": url,
                               "source": "downloader"})

    # -- istek ve kuyruk ---------------------------------------------------
    def default_output_dir(self) -> Path:
        """Açık projenin ``01_Downloads`` klasörü; proje yoksa varsayılan kökün altında."""
        project = self.ctx.project
        if project.is_open and project.root is not None:
            return Path(project.root) / folders.folder_for("download")   # yalnızca yol; oluşturma işleyicide
        root = str(self.ctx.settings.get("project_root") or "")
        base = Path(root).expanduser() if root else Path.home() / "Videos"
        return base / folders.folder_for("download")

    def build_request(self, *, duration: Optional[float] = None,
                      with_output: bool = True) -> DownloadRequest:
        d = self.draft
        start = d.start if d.partial else 0.0
        end = d.end if d.partial else None
        if start < 0.5:
            start = 0.0
        if end is not None and duration and end >= duration - 0.5:
            end = None
        if end is not None and end <= start + 1:
            raise ValueError(i18n.t(K.RANGE_INVALID))
        return DownloadRequest(url=d.url, mode=d.mode, quality=d.quality, format_ids=d.format_ids,
                               start=start, end=end, options=d.options,
                               output_dir=str(self.default_output_dir()) if with_output else "")

    def submit(self, request: DownloadRequest, *, title: str = "") -> Job:
        if self.ctx is None:
            raise RuntimeError("İndirici servisi başlatılmadı")
        return self.ctx.jobs.submit(JOB_KIND, request.to_payload(), title or request.url,
                                    notify=True, max_retries=1)

    def submit_playlist(self, entries: Sequence[EntryRef], request: DownloadRequest) -> List[Job]:
        jobs: List[Job] = []
        for entry in entries:
            sub = replace(request, url=entry.url, options=replace(request.options, playlist=False))
            jobs.append(self.submit(sub, title=entry.title or entry.url))
        return jobs

    def cancel(self, job_id: int) -> bool:
        return bool(self.ctx and self.ctx.jobs.cancel(job_id))

    def recent(self, limit: int = 8) -> List[Dict[str, Any]]:
        """Son indirme işleri (en yeni başta): başlık, durum, ilerleme, dosya yolu."""
        if self.ctx is None:
            return []
        jobs = [j for j in self.ctx.jobs.list() if j.kind == JOB_KIND]
        jobs.sort(key=lambda j: j.updated, reverse=True)
        rows: List[Dict[str, Any]] = []
        for j in jobs[:max(limit, 0)]:
            result = j.payload.get("_result") if isinstance(j.payload.get("_result"), Mapping) else {}
            rows.append({"id": j.id, "title": j.title, "status": j.status,
                         "progress": float(j.progress or 0.0), "path": str(result.get("path") or ""),
                         "error": j.error or ""})
        return rows

    # -- işleyici (iş parçacığı) -------------------------------------------
    def _handle_download(self, job: Job, jc: Any) -> Dict[str, Any]:
        req = DownloadRequest.from_payload(job.payload)
        out_dir = Path(req.output_dir).expanduser() if req.output_dir else self.default_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        card = disk.warning_for(out_dir)
        if card is not None and card.code == "disk.critical":
            raise card

        def hook(d: Dict[str, Any]) -> None:
            if jc.is_cancelled():
                raise _Cancelled()
            status = d.get("status")
            if status == "downloading":
                total = _num(d.get("total_bytes") or d.get("total_bytes_estimate"))
                done = _num(d.get("downloaded_bytes"))
                speed = _num(d.get("speed"))
                if total > 0:
                    text = i18n.t(K.PROGRESS_DOWNLOADING, speed=disk.human_size(int(speed)) + "/s")
                    jc.report(min(0.95, done / total), text)
            elif status == "finished":
                jc.report(0.97, i18n.t(K.PROGRESS_PROCESSING))

        opts = build_ydl_options(req, out_dir=out_dir, progress_hook=hook)
        try:
            with self._ydl(opts) as ydl:
                info = ydl.extract_info(req.url, download=True)
                files = _collect_files(info or {}, ydl, req.mode)
        except _Cancelled:
            return {}
        except SmartError:
            raise
        except Exception as exc:  # noqa: BLE001 — işleyici hatası SmartError'a çevrilir
            raise self.translate_error(exc, req.url) from exc
        jc.report(1.0, "")
        return {"path": files[0] if files else "", "files": files, "folder": str(out_dir)}
