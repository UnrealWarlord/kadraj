"""Android hata günlüğü (``kadraj/crashlog.py``): açılış hatası ekranda görünür ve Belgeler/KADRAJ-hata.txt'ye yazılır."""
from __future__ import annotations

import faulthandler
import importlib.util
import sys
from pathlib import Path

import pytest

from kadraj import crashlog

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fresh(monkeypatch):
    """Her test temiz durumla başlar; sys.excepthook test sonunda geri gelir."""
    monkeypatch.setattr(crashlog, "_state", {"home": None, "fault_file": None, "hook": None})
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    yield
    fh = crashlog._state.get("fault_file")
    if fh is not None:
        fh.close()


def _raise_into_hook(exc: BaseException) -> None:
    try:
        raise exc
    except type(exc):
        sys.excepthook(*sys.exc_info())


@pytest.mark.qt_no_exception_capture                       # pytest-qt'nin kendi kancası "önceki kanca" olmasın
def test_uncaught_errors_go_to_the_log_and_the_previous_hook(tmp_path, fresh, capsys):
    crashlog.install(tmp_path, fault=False)
    _raise_into_hook(ValueError("örnek hata"))
    text = (tmp_path / crashlog.FILE_NAME).read_text(encoding="utf-8")
    assert "ValueError: örnek hata" in text and "· KADRAJ " in text
    assert "ValueError: örnek hata" in capsys.readouterr().err          # önceki kanca da çalıştı (konsol/logcat)


def test_installing_twice_wraps_the_hook_once_and_moves_the_log(tmp_path, fresh):
    crashlog.install(tmp_path / "a", fault=False)
    hook = sys.excepthook
    crashlog.install(tmp_path / "b", fault=False)
    assert sys.excepthook is hook
    assert crashlog.log_path() == tmp_path / "b" / crashlog.FILE_NAME


def test_native_crashes_are_written_to_the_same_file(tmp_path, fresh, monkeypatch):
    calls = []
    monkeypatch.setattr(faulthandler, "enable", lambda f, all_threads=False: calls.append((f.name, all_threads)))
    crashlog.install(tmp_path)
    crashlog.install(tmp_path)                                         # ikinci çağrı yeni dosya açmaz
    assert calls == [(str(tmp_path / crashlog.FILE_NAME), True)]


def test_unwritable_log_does_not_raise(tmp_path, fresh):
    blocker = tmp_path / "dosya"
    blocker.write_text("x", encoding="utf-8")
    assert crashlog.append("hata", home=blocker / "alt") is None        # klasör yerine dosya: yazılamaz, hata atılmaz


def test_fatal_startup_error_is_logged_and_shown(tmp_path, fresh, monkeypatch, qapp):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.informativeText()) or 0)
    crashlog.install(tmp_path, fault=False)
    path = crashlog.show_fatal("Traceback (most recent call last):\n" + "x\n" * 2000 + "ImportError: numpy yok")
    assert path == tmp_path / crashlog.FILE_NAME and "ImportError: numpy yok" in path.read_text(encoding="utf-8")
    assert len(shown) == 1 and "ImportError: numpy yok" in shown[0] and str(path) in shown[0]
    assert len(shown[0]) < crashlog.SHOW_CHARS + len(str(path)) + 10   # uzun geri izlemenin yalnız sonu gösterilir


def _load_main():
    spec = importlib.util.spec_from_file_location("kadraj_main_for_test", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_android_main_shows_startup_errors_instead_of_a_black_screen(monkeypatch):
    main = _load_main()
    monkeypatch.setenv("KADRAJ_PLATFORM", "android")
    shown = []
    monkeypatch.setattr(crashlog, "show_fatal", lambda text: shown.append(text))

    def boom(*a, **k):
        raise RuntimeError("açılışta patladı")

    monkeypatch.setattr("kadraj.ui.shell.app.run", boom)
    assert main.main([]) == 1
    assert len(shown) == 1 and "RuntimeError: açılışta patladı" in shown[0]


def test_desktop_main_does_not_swallow_startup_errors(monkeypatch):
    main = _load_main()
    monkeypatch.setenv("KADRAJ_PLATFORM", "desktop")

    def boom(*a, **k):
        raise RuntimeError("masaüstünde görünür kalmalı")

    monkeypatch.setattr("kadraj.ui.shell.app.run", boom)
    with pytest.raises(RuntimeError):
        main.main([])


def test_note_is_one_branded_line_on_stdout(capsys):
    from kadraj.branding import APP_NAME
    crashlog.note("ana pencere açıldı (simple)")
    assert capsys.readouterr().out == f"{APP_NAME}: ana pencere açıldı (simple)\n"
