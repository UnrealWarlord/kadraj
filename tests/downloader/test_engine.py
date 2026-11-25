"""Motor: kalite uygunluğu, açıklayıcı hata kartları, yt-dlp'ye verilen ffmpeg/Deno/EJS seçenekleri."""
import pytest

from kadraj.core import components
from kadraj.modules.downloader import errors as E
from kadraj.modules.downloader import service as S


def _info(*heights, width_for=None, youtube=True):
    fmts = tuple(S.FormatRow(id=str(h), ext="mp4", resolution=f"{h}p", height=h, fps=30, vcodec="avc1", acodec="none",
                             size=None, tbr=1000, abr=0, note="", width=(width_for or {}).get(h, h * 16 // 9)) for h in heights)
    return S.VideoInfo(url="https://x/y", title="t", extractor="youtube" if youtube else "generic", duration=60,
                       max_height=max(heights, default=0), subtitles=(), is_playlist=False, entries=(), formats=fmts)


def test_unavailable_qualities_are_flagged_with_actual_height():
    st = {q.key: q for q in S.quality_states(_info(144, 360, 480))}
    assert not st["fast"].available and not st["balanced"].available and not st["best"].available
    assert st["fast"].actual_height == 480                        # 720 isteyene gerçekte 480p iner
    assert S.best_available_quality(_info(144, 360, 480)) == "fast"
    full = S.available_qualities(_info(360, 720, 1080))
    assert full == {"fast": True, "balanced": True, "best": False}
    assert S.best_available_quality(_info(360, 720, 1080)) == "balanced"
    assert S.fit_quality(_info(360, 720, 1080), "best") == "balanced"
    assert S.fit_quality(_info(360, 720), "balanced") == "fast"


def test_ultrawide_4k_counts_by_width():
    assert S.available_qualities(_info(1608, width_for={1608: 3840}))["best"] is True


def test_unknown_info_keeps_all_qualities_enabled():
    assert all(S.available_qualities(None).values())


@pytest.mark.parametrize("msg,expected", [
    ("[youtube] x: n challenge solving failed: Some formats may be missing", "JS"),
    ("No supported JavaScript runtime could be found", "JS"),
    ("ERROR: ffprobe and ffmpeg not found. Please install or provide the path using --ffmpeg-location", "FFMPEG"),
    ("ERROR: [youtube] x: Sign in to confirm you're not a bot", "BOT"),
    ("ERROR: Unable to extract uploader id; please report this issue on the tracker", "OUTDATED"),
])
def test_errors_get_a_plain_card_with_numbered_steps_and_an_action(msg, expected, monkeypatch):
    monkeypatch.setattr(components, "have", lambda _n: True)
    card = E.explain(RuntimeError(msg))
    assert card is not None and card.code == f"downloader.{expected.lower()}"
    assert card.title and card.summary and card.cause
    assert [s[:2] for s in card.steps] == ["1.", "2.", "3."]
    assert card.actions and card.actions[0].id == "update_components" and card.actions[0].payload["topic"] == "components.update"
    assert card.title.lower() != "olmadı"


def test_format_error_on_youtube_without_deno_points_at_the_js_runtime(monkeypatch):
    monkeypatch.setattr(components, "have", lambda _n: False)
    card = E.explain(RuntimeError("[youtube] x: Requested format is not available"))
    assert card.code == "downloader.js"
    monkeypatch.setattr(components, "have", lambda _n: True)
    assert E.explain(RuntimeError("[generic] Requested format is not available")).code == "downloader.format"


def test_unknown_error_falls_through_to_the_generic_translator():
    assert E.explain(RuntimeError("tamamen bilinmeyen")) is None


def test_engine_options_pass_ffmpeg_dir_deno_and_ejs(monkeypatch, tmp_path):
    monkeypatch.setattr(components, "ffmpeg_location", lambda: str(tmp_path))
    monkeypatch.setattr(components, "deno_path", lambda: str(tmp_path / "deno"))
    o = S.engine_options()
    assert o["ffmpeg_location"] == str(tmp_path)
    assert o["js_runtimes"] == {"deno": {"path": str(tmp_path / "deno")}}
    assert "ejs:github" in o["remote_components"]
    monkeypatch.setattr(components, "ffmpeg_location", lambda: None)
    monkeypatch.setattr(components, "deno_path", lambda: None)
    assert "ffmpeg_location" not in S.engine_options() and "js_runtimes" not in S.engine_options()


def test_without_ffmpeg_falls_back_to_a_single_file_format_or_explains():
    req = S.DownloadRequest(url="https://x/y", quality=S.QUALITY_BALANCED)
    out = S.DownloaderService._without_ffmpeg(req, {"format": "bestvideo+bestaudio", "merge_output_format": "mp4"})
    assert out["format"].startswith("best[height<=1080]") and "merge_output_format" not in out
    from kadraj.core.contracts import SmartError
    with pytest.raises(SmartError) as ei:
        S.DownloaderService._without_ffmpeg(S.DownloadRequest(url="https://x/y", mode=S.MODE_AUDIO), {})
    assert ei.value.code == "downloader.ffmpeg"
