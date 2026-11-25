"""Düzenleyici test düzeni: sentetik medya (``testsrc``/``sine``), modül dizeleri yüklü, ağ kapalı, donanım sahte."""
from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from kadraj.core import i18n
from kadraj.modules.editor import render
from kadraj.modules.editor.strings import STRINGS

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg yok")


@pytest.fixture(autouse=True)
def _editor_strings():
    i18n.register_strings(STRINGS)
    i18n.set_language("tr")
    yield


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(self, *a, **k):
        raise OSError("ağ kapalı (test)")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("dns kapalı")))


@pytest.fixture(autouse=True)
def fake_hw():
    """Varsayılan: donanım yok (CPU). Testler isterse ``render.set_hardware_override`` ile değiştirir."""
    render.set_hardware_override({"nvenc": False, "vaapi": False})
    yield
    render.set_hardware_override(None)


def _ff(*args: str) -> None:
    subprocess.run([FFMPEG, "-v", "error", "-y", *args], check=True, capture_output=True, timeout=60)


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict:
    """160×90@10 fps, 3 sn: ``a.mp4`` (testsrc + 440 Hz), ``b.mp4`` (testsrc2 + 880 Hz), ``v.mp4`` (sessiz), ``music.wav``, ``voice.wav``, ``pic.png``."""
    if FFMPEG is None:
        pytest.skip("ffmpeg yok")
    d = tmp_path_factory.mktemp("media")
    _ff("-f", "lavfi", "-i", "testsrc=s=160x90:r=10:d=3", "-f", "lavfi", "-i", "sine=f=440:d=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "a.mp4"))
    _ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=3", "-f", "lavfi", "-i", "sine=f=880:d=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "b.mp4"))
    _ff("-f", "lavfi", "-i", "testsrc=s=160x90:r=10:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "v.mp4"))
    _ff("-f", "lavfi", "-i", "sine=f=220:d=4", "-ar", "48000", "-ac", "2", str(d / "music.wav"))
    _ff("-f", "lavfi", "-i", "sine=f=1000:d=2", "-ar", "48000", "-ac", "2", str(d / "voice.wav"))
    _ff("-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.1", "-frames:v", "1", str(d / "pic.png"))
    return {k: str(d / f) for k, f in dict(a="a.mp4", b="b.mp4", v="v.mp4", music="music.wav", voice="voice.wav", pic="pic.png").items()}
