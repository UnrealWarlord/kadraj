"""Paketlenmiş uygulamaya özgü alt süreç düzeltmeleri (``kadraj.frozen``)."""
from __future__ import annotations

import subprocess
import sys

import pytest

from kadraj import frozen

_PRINT_LIB = "import os; print(os.environ.get('LD_LIBRARY_PATH', 'YOK'))"


def test_clean_env_restores_original_library_path():
    env = {"LD_LIBRARY_PATH": "/paket/_internal:/eski", "LD_LIBRARY_PATH_ORIG": "/eski", "PATH": "/bin"}
    out = frozen.clean_env(env)
    assert out["LD_LIBRARY_PATH"] == "/eski" and "LD_LIBRARY_PATH_ORIG" not in out and out["PATH"] == "/bin"
    assert env["LD_LIBRARY_PATH"] == "/paket/_internal:/eski"             # girdi değişmez


def test_clean_env_drops_variable_that_was_unset_originally():
    out = frozen.clean_env({"LD_LIBRARY_PATH": "/paket/_internal", "PATH": "/bin"})   # *_ORIG yok → özgün ortamda tanımsızdı
    assert "LD_LIBRARY_PATH" not in out and out["PATH"] == "/bin"


def test_apply_is_a_noop_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    before = subprocess.Popen.__init__
    assert frozen.apply() is False
    assert subprocess.Popen.__init__ is before


@pytest.mark.skipif(sys.platform == "win32", reason="LD_LIBRARY_PATH Linux/Unix'e özgü")
def test_apply_gives_child_processes_the_original_environment(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/paket/_internal:/eski")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/eski")
    try:
        assert frozen.apply() is True and frozen.apply() is True            # ikinci çağrı yamayı katlamaz
        out = subprocess.run([sys.executable, "-c", _PRINT_LIB], capture_output=True, text=True, check=True).stdout.strip()
        assert out == "/eski"
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
        out = subprocess.run([sys.executable, "-c", _PRINT_LIB], capture_output=True, text=True, check=True).stdout.strip()
        assert out == "YOK"
    finally:
        frozen.revert()
    assert not getattr(subprocess.Popen.__init__, "_kadraj_frozen_patch", False)


def test_apply_hides_console_windows_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    seen = {}
    real_init = subprocess.Popen.__init__

    def spy(self, *a, **kw):
        seen.update(kw)                                                      # gerçek Popen çalıştırılmaz (Linux'ta creationflags geçersiz)

    monkeypatch.setattr(subprocess.Popen, "__init__", spy)
    try:
        assert frozen.apply() is True
        subprocess.Popen(["ffmpeg"], creationflags=0x200)                    # çağıranın bayrağı korunur
    finally:
        frozen.revert()
        monkeypatch.setattr(subprocess.Popen, "__init__", real_init)
    assert seen["creationflags"] == 0x200 | 0x08000000 and "env" not in seen
