"""Saf yardımcılar: zaman kodları, biçim seçici, yt-dlp seçenekleri, komut önizlemesi, boyut tahmini."""
from __future__ import annotations

from pathlib import Path

import pytest

from kadraj.modules.downloader import service as S

from .fakes import PLAYLIST_INFO, SAMPLE_INFO


def test_timecodes_parse_and_format():
    assert S.parse_timecode("1:20") == 80
    assert S.parse_timecode("01:02:03") == 3723
    assert S.parse_timecode("80") == 80
    assert S.parse_timecode("1:20.5") == 80.5
    assert S.parse_timecode("1,5") == 1.5
    assert S.format_timecode(80) == "1:20"
    assert S.format_timecode(80, full=True) == "00:01:20"
    assert S.format_timecode(3723) == "01:02:03"          # bir saatten uzun: HH:MM:SS
    assert S.describe_length(145) == "2 dk 25 sn"
    assert S.describe_length(0) == "0 sn"
    assert S.describe_length(3700) == "1 sa 1 dk 40 sn"


@pytest.mark.parametrize("bad", ["", "abc", "1:99", "-1", "nan", "1:2:3:4", "inf"])
def test_invalid_timecodes_raise(bad):
    with pytest.raises(ValueError):
        S.parse_timecode(bad)


def test_rate_parsing():
    assert S.parse_rate("5M") == 5 * 1024 * 1024
    assert S.parse_rate("500k") == 500 * 1024
    assert S.parse_rate("") is None
    assert S.parse_rate("fast") is None


def test_format_selector_per_mode_and_quality():
    assert S.format_selector("av", "balanced") == "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    assert S.format_selector("av", "fast").startswith("bestvideo[height<=720]")
    assert S.format_selector("av", "best").startswith("bestvideo[height<=2160]")
    assert S.format_selector("audio", "fast") == "bestaudio/best"
    assert S.format_selector("video", "balanced") == "bestvideo[height<=1080]/bestvideo"
    assert S.format_selector("video", "best", ["137", "140"]) == "137+140"


def test_url_check():
    assert S.is_probable_url("https://www.youtube.com/watch?v=abc")
    assert not S.is_probable_url("ftp://x.y/z")
    assert not S.is_probable_url("merhaba")


def test_ydl_options_for_av_and_audio(tmp_path: Path):
    req = S.DownloadRequest(url="https://a.example/v")
    av = S.build_ydl_options(req, out_dir=tmp_path)
    assert av["merge_output_format"] == "mp4"
    assert av["postprocessors"] == []
    assert "download_ranges" not in av
    assert "%(id)s" in av["outtmpl"] and str(tmp_path) in av["outtmpl"]
    assert av["noplaylist"] is True

    audio = S.build_ydl_options(S.DownloadRequest(url="u", mode=S.MODE_AUDIO), out_dir=tmp_path)
    assert {"key": "FFmpegExtractAudio", "preferredcodec": "wav"} in audio["postprocessors"]
    args = audio["postprocessor_args"]["ExtractAudio"]
    assert args[args.index("-ar") + 1] == "48000" and "pcm_s24le" in args
    assert "merge_output_format" not in audio


def test_ydl_options_range_subtitles_thumbnail_playlist_rate(tmp_path: Path):
    opts = S.Options(subtitles=True, sub_langs=("tr", "en"), thumbnail=True, embed_thumbnail=True,
                     playlist=True, rate_limit="5M")
    req = S.DownloadRequest(url="u", start=80.0, end=225.0, options=opts)
    built = S.build_ydl_options(req, out_dir=tmp_path)
    assert callable(built["download_ranges"])
    assert built["force_keyframes_at_cuts"] is True
    assert built["writesubtitles"] is True and built["subtitleslangs"] == ["tr", "en"]
    assert {"key": "FFmpegSubtitlesConvertor", "format": "srt"} in built["postprocessors"]
    assert built["writethumbnail"] is True
    assert {"key": "EmbedThumbnail"} in built["postprocessors"]
    assert built["noplaylist"] is False
    assert built["ratelimit"] == 5 * 1024 * 1024


def test_embed_thumbnail_is_ignored_for_audio_only(tmp_path: Path):
    opts = S.Options(embed_thumbnail=True)
    built = S.build_ydl_options(S.DownloadRequest(url="u", mode=S.MODE_AUDIO, options=opts), out_dir=tmp_path)
    assert {"key": "EmbedThumbnail"} not in built["postprocessors"]


def test_command_preview_mirrors_options():
    req = S.DownloadRequest(url="https://a.example/v?x=1", mode=S.MODE_AUDIO, start=80.0, end=225.0,
                            options=S.Options(subtitles=True, rate_limit="5M"))
    cmd = S.command_preview(req)
    assert cmd.startswith("yt-dlp -f ")
    assert "--download-sections" in cmd and "00:01:20-00:03:45" in cmd
    assert "--extract-audio --audio-format wav" in cmd
    assert "--write-subs --sub-langs tr" in cmd and "--limit-rate 5M" in cmd


def test_info_parsing_for_video_and_playlist():
    info = S.info_from_raw(SAMPLE_INFO, "https://www.youtube.com/watch?v=abc123")
    assert info.title == "Örnek Klip" and info.site_name == "YouTube" and info.is_youtube
    assert info.duration == 225 and info.max_height == 2160
    assert info.subtitles == ("en", "tr")
    assert [f.id for f in info.formats] == ["18", "137", "313", "140"]
    assert info.formats[3].resolution == "—" and info.formats[1].resolution == "1080p"

    pl = S.info_from_raw(PLAYLIST_INFO, "https://www.youtube.com/playlist?list=x")
    assert pl.is_playlist and len(pl.entries) == 2
    assert pl.entries[0].url == "https://www.youtube.com/watch?v=a1"
    assert pl.entries[1].title == "İki"


def test_info_parsing_tolerates_junk():
    with pytest.raises(ValueError):
        S.info_from_raw("bozuk", "u")
    info = S.info_from_raw({"title": "x", "formats": [None, "y", {"format_id": "1"}]}, "u")
    assert len(info.formats) == 1 and info.duration == 0


def test_size_estimates():
    info = S.info_from_raw(SAMPLE_INFO, "u")
    # 1080p video (80 MB) + 128 kbps ses (4 MB)
    assert S.estimate_bytes(info, "av", "balanced") == 84_000_000
    # yarısı → yarısı
    assert S.estimate_bytes(info, "av", "balanced", 0.0, 112.5) == 42_000_000
    # 720p sınırında birleşik biçim (360p, 10 MB)
    assert S.estimate_bytes(info, "av", "fast") == 10_000_000
    # 4K'da yalnız görüntü: 300 MB (filesize_approx)
    assert S.estimate_bytes(info, "video", "best") == 300_000_000
    # ses: 48 kHz · 2 kanal · 24 bit · 225 sn
    assert S.estimate_bytes(info, "audio", "best") == 225 * 288_000
    assert S.estimate_bytes(S.info_from_raw(PLAYLIST_INFO, "u"), "av", "best") is None
