"""``core/components.py``: arama sırası (env → güncellenmiş → paketle gelen → PATH) ve yt-dlp seçimi."""
import json
import os
import sys
import zipfile

import pytest

from kadraj.core import components as C


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    home, bundled = tmp_path / "home", tmp_path / "bundled"
    (home / "tools").mkdir(parents=True)
    bundled.mkdir()
    monkeypatch.setenv("KADRAJ_HOME", str(home))
    monkeypatch.setenv("KADRAJ_BUNDLED_TOOLS", str(bundled))
    for var in ("KADRAJ_FFMPEG", "KADRAJ_FFPROBE", "KADRAJ_DENO", "KADRAJ_YTDLP"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(C.shutil, "which", lambda _n: None)
    return home / "tools", bundled


def _exe(folder, name):
    p = folder / C.exe_name(name)
    p.write_bytes(b"#!/bin/sh\n")
    os.chmod(p, 0o755)
    return p


def test_nothing_found_returns_none(dirs):
    assert C.find("ffmpeg") is None and C.ffmpeg_path() is None and not C.have("deno")


def test_order_env_then_updated_then_bundled_then_system(dirs, tmp_path, monkeypatch):
    tools, bundled = dirs
    monkeypatch.setattr(C.shutil, "which", lambda n: f"/usr/bin/{n}")
    assert C.find("ffmpeg").source == C.SOURCE_SYSTEM
    _exe(bundled, "ffmpeg")
    assert C.find("ffmpeg").source == C.SOURCE_BUNDLED
    _exe(tools, "ffmpeg")
    assert C.find("ffmpeg").source == C.SOURCE_UPDATED
    mine = tmp_path / "benim-ffmpeg"
    mine.write_bytes(b"x")
    os.chmod(mine, 0o755)
    monkeypatch.setenv("KADRAJ_FFMPEG", str(mine))
    hit = C.find("ffmpeg")
    assert hit.source == C.SOURCE_ENV and hit.path == str(mine)


def test_versions_file_gives_recorded_version_and_ffmpeg_location(dirs):
    tools, _ = dirs
    _exe(tools, "ffmpeg")
    _exe(tools, "ffprobe")
    (tools / C.VERSIONS_FILE).write_text(json.dumps({"ffmpeg": {"version": "9.0"}}), encoding="utf-8")
    assert C.find("ffmpeg").version == "9.0" and C.find("ffprobe").version == "9.0"   # ffmpeg+ffprobe tek grup
    assert C.ffmpeg_location() == str(tools)


def test_ytdlp_never_comes_from_path(dirs, monkeypatch):
    monkeypatch.setattr(C.shutil, "which", lambda n: f"/usr/bin/{n}")
    assert C.find("yt-dlp") is None            # PATH'teki yt-dlp bir program; içe aktarılamaz


def _zipapp(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("yt_dlp/__init__.py", "")
        z.writestr("__main__.py", "")


def test_ytdlp_picks_newest_between_updated_bundled_and_module(dirs, monkeypatch):
    tools, bundled = dirs
    _zipapp(tools / "yt-dlp")
    _zipapp(bundled / "yt-dlp")
    (tools / C.VERSIONS_FILE).write_text(json.dumps({"yt-dlp": {"version": "2026.11.23"}}), encoding="utf-8")
    (bundled / C.VERSIONS_FILE).write_text(json.dumps({"yt-dlp": {"version": "2026.10.01"}}), encoding="utf-8")
    monkeypatch.setattr(C, "module_ytdlp_version", lambda: "2026.09.01")
    assert C.ytdlp_choice().source == C.SOURCE_UPDATED
    (tools / C.VERSIONS_FILE).write_text(json.dumps({"yt-dlp": {"version": "2026.08.01"}}), encoding="utf-8")
    assert C.ytdlp_choice().source == C.SOURCE_BUNDLED
    monkeypatch.setattr(C, "module_ytdlp_version", lambda: "2027.01.01")
    assert C.ytdlp_choice().source == C.SOURCE_MODULE


def test_prepare_ytdlp_puts_zipapp_first_on_sys_path(dirs, monkeypatch):
    tools, _ = dirs
    _zipapp(tools / "yt-dlp")
    (tools / C.VERSIONS_FILE).write_text(json.dumps({"yt-dlp": {"version": "2999.01.01"}}), encoding="utf-8")
    monkeypatch.delitem(sys.modules, "yt_dlp", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    choice = C.prepare_ytdlp()
    assert choice.source == C.SOURCE_UPDATED and sys.path[0] == str(tools / "yt-dlp")
