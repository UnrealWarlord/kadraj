"""Açılış ekranı + bileşen güncelleme: kısa işler bitene (ya da bütçe/"Atla") kadar bekler, uzun indirmeler beklemez."""
import sys
import threading

from PySide6.QtTest import QTest

from kadraj.core import updater
from kadraj.ui.shell import splash as splash_mod
from kadraj.ui.shell.splash import SplashScreen


class FakeRun:
    """``updater.BackgroundRun`` yerine: ağa çıkmaz; ``small_done`` testten elle set edilir."""

    def __init__(self):
        self.small_done = threading.Event()
        self.done = threading.Event()
        self.text = "yt-dlp 2026.11.23 indiriliyor…"
        self.fraction = 0.4
        self.report = None

    def start(self):
        return self


def _patch(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(splash_mod.updater, "BackgroundRun", lambda *a, **k: fake)
    return fake


def test_updates_off_by_default_no_run_no_button(ctx, qtbot):
    sp = SplashScreen(120)
    qtbot.addWidget(sp)
    with qtbot.waitSignal(sp.finished, timeout=3000):
        sp.start()
    assert sp._run is None and not sp.skip_btn.isVisibleTo(sp)


def test_splash_waits_for_quick_phase_then_closes(ctx, qtbot, monkeypatch):
    fake = _patch(monkeypatch)
    sp = SplashScreen(100, updates=True)
    qtbot.addWidget(sp)
    sp.start()
    QTest.qWait(400)
    assert sp.isVisible() and sp.progress() <= 0.95                 # süre doldu ama denetim sürüyor → açık kalır
    assert "yt-dlp" in sp.status.text() and sp.skip_btn.isVisibleTo(sp)
    with qtbot.waitSignal(sp.finished, timeout=3000):
        fake.small_done.set()                                       # kısa işler bitti; büyük indirme sürse de kapanır


def test_atla_button_releases_the_splash_but_the_run_continues(ctx, qtbot, monkeypatch):
    fake = _patch(monkeypatch)
    sp = SplashScreen(100, updates=True)
    qtbot.addWidget(sp)
    sp.start()
    QTest.qWait(300)
    assert sp.isVisible()
    with qtbot.waitSignal(sp.finished, timeout=3000):
        sp.skip_btn.click()
    assert not fake.done.is_set()                                   # güncelleyici arka planda sürüyor


def test_budget_ends_the_wait_even_if_network_hangs(ctx, qtbot, monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(splash_mod, "UPDATE_BUDGET_MS", 300)
    sp = SplashScreen(100, updates=True)
    qtbot.addWidget(sp)
    with qtbot.waitSignal(sp.finished, timeout=3000):
        sp.start()


def test_auto_update_is_off_in_tests_and_when_switched_off(monkeypatch):
    assert updater.auto_update_enabled() is False                   # pytest altında hiç ağa çıkılmaz
    monkeypatch.delitem(sys.modules, "pytest")                      # (teardown'da geri konur)
    monkeypatch.delenv("KADRAJ_NO_UPDATE", raising=False)
    assert updater.auto_update_enabled() is True
    assert updater.auto_update_enabled(type("S", (), {"get": lambda self, k, d=None: False})()) is False
    monkeypatch.setenv("KADRAJ_NO_UPDATE", "1")
    assert updater.auto_update_enabled() is False
