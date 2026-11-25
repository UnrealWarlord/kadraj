"""Sahte yt-dlp: ağ yok, ffmpeg yok. Testler ``FakeYDL.scenario`` ile davranışı belirler."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

SAMPLE_INFO: Dict[str, Any] = {
    "_type": "video",
    "id": "abc123",
    "title": "Örnek Klip",
    "extractor": "youtube",
    "extractor_key": "Youtube",
    "webpage_url": "https://www.youtube.com/watch?v=abc123",
    "duration": 225,
    "subtitles": {"tr": [], "en": []},
    "formats": [
        {"format_id": "18", "ext": "mp4", "height": 360, "fps": 30, "vcodec": "avc1.42001E",
         "acodec": "mp4a.40.2", "filesize": 10_000_000, "format_note": "360p"},
        {"format_id": "137", "ext": "mp4", "height": 1080, "fps": 30, "vcodec": "avc1.640028",
         "acodec": "none", "filesize": 80_000_000, "tbr": 2500, "format_note": "1080p"},
        {"format_id": "313", "ext": "webm", "height": 2160, "fps": 30, "vcodec": "vp9",
         "acodec": "none", "filesize_approx": 300_000_000, "tbr": 9000, "format_note": "2160p"},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
         "abr": 128, "filesize": 4_000_000, "format_note": "medium"},
    ],
}

PLAYLIST_INFO: Dict[str, Any] = {
    "_type": "playlist",
    "title": "Liste",
    "extractor": "youtube:tab",
    "entries": [
        {"id": "a1", "title": "Bir", "url": "a1", "ie_key": "Youtube"},
        {"id": "b2", "title": "İki", "webpage_url": "https://www.youtube.com/watch?v=b2"},
        None,
    ],
}


def default_download(url: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    """Diske küçük bir dosya yazar ve ilerleme kancalarını çağırır (ffmpeg yok)."""
    out = Path(opts["outtmpl"]).parent
    out.mkdir(parents=True, exist_ok=True)
    ident = url.rsplit("=", 1)[-1] or "video"
    path = out / f"Örnek Klip [{ident}].mp4"
    # Gerçek indirici gibi: önce ilerleme (iptal burada yakalanır), sonra dosya.
    for hook in opts.get("progress_hooks") or []:
        hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100, "speed": 1000.0})
    path.write_bytes(b"x" * 100)
    for hook in opts.get("progress_hooks") or []:
        hook({"status": "finished", "filename": str(path)})
    return {"id": ident, "title": "Örnek Klip", "filepath": str(path)}


def default_scenario() -> Dict[str, Any]:
    return {"info": dict(SAMPLE_INFO), "download": default_download, "error": None}


class FakeYDL:
    scenario: Dict[str, Any] = default_scenario()
    opts_seen: List[Dict[str, Any]] = []

    def __init__(self, opts: Dict[str, Any]) -> None:
        self.opts = dict(opts)
        FakeYDL.opts_seen.append(self.opts)

    def __enter__(self) -> "FakeYDL":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> Any:
        s = FakeYDL.scenario
        if s.get("error") is not None:
            raise s["error"]
        if download:
            return s["download"](url, self.opts)
        return dict(s["info"])

    def prepare_filename(self, _info: Dict[str, Any]) -> str:
        return str(Path(self.opts["outtmpl"]).parent / "tahmin.mp4")


class FakeJC:
    """``JobContext`` taklidi: ilerlemeyi kaydeder, iptal bayrağını döner."""

    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled
        self.reports: List[tuple] = []

    def report(self, progress: float, message: str = "") -> None:
        self.reports.append((progress, message))

    def is_cancelled(self) -> bool:
        return self.cancelled


class FakeJob:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self.payload = payload
        self.kind = "download"
        self.id = 1
        self.title = "test"
