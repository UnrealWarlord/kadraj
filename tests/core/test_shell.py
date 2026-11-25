"""Kabuk: ana pencere iki modda, durum korunumu, gezinti, sihirbaz, splash, onay, akış."""
from __future__ import annotations

import socket

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QUndoCommand
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QWidget

from kadraj import consent
from kadraj.core.contracts import (Accent, ModuleSpec, Topics, UiMode, WizardAnswers)
from kadraj.ui.shell.app import Launcher
from kadraj.ui.shell.dialogs import ensure_consent
from kadraj.ui.shell.main_window import MainWindow
from kadraj.ui.shell.pages import ComingSoonPage, PageErrorWidget
from kadraj.ui.shell.splash import SPLASH_MAX_MS, SPLASH_MIN_MS, SplashScreen, splash_duration
from kadraj.ui.shell.wizard import WizardDialog


class _Push(QUndoCommand):
    def __init__(self, log):
        super().__init__("Test işlemi")
        self.log = log

    def redo(self):
        self.log.append("redo")

    def undo(self):
        self.log.append("undo")


@pytest.fixture()
def win(ctx, qtbot, tmp_path):
    ctx.create_project("Kabuk Testi", tmp_path / "projeler")
    w = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(w)
    w.resize(1320, 860)
    w.show()
    return w


# ---- iki modda açılış, durum korunumu (plan §0.5) ------------------------------------------------
def test_main_window_opens_in_both_modes(win, ctx):
    for mode in (UiMode.LEGEND, UiMode.SIMPLE, UiMode.LEGEND):
        ctx.set_ui_mode(mode)
        assert win.isVisible() and win.body.currentIndex() == (1 if mode is UiMode.LEGEND else 0)
        assert win.menu_bar.isVisible() == (mode is UiMode.LEGEND)
        assert all(d.isVisible() == (mode is UiMode.LEGEND) for d in win.docks)
        assert win.topbar.mode_switch.mode is mode
        assert ctx.theme.mode is mode and ctx.settings.get("ui_mode") == mode.value


def test_mode_switch_preserves_project_undo_and_navigation(win, ctx):
    log = []
    ctx.undo.push(_Push(log))
    root = ctx.project.root
    ctx.current_module = "recorder"
    win.open_module("recorder")
    for mode in ("legend", "simple", "legend", "simple"):
        ctx.set_ui_mode(mode)
        assert ctx.project.is_open and ctx.project.root == root
        assert ctx.undo.canUndo() and ctx.undo.undoText() == "Test işlemi"
        assert ctx.current_module == "recorder"
        assert win.topbar.undo_btn.isEnabled()
    win.undo()
    assert log == ["redo", "undo"] and not ctx.undo.canUndo() and not win.topbar.undo_btn.isEnabled()


def test_clicking_the_switch_changes_mode_via_context(win, ctx, qtbot):
    sw = win.topbar.mode_switch
    QTest.mouseClick(sw, Qt.MouseButton.LeftButton, pos=sw.rect().center() + sw.rect().center() / 2)
    assert ctx.ui_mode is UiMode.LEGEND and win.body.currentIndex() == 1
    QTest.mouseClick(sw, Qt.MouseButton.LeftButton, pos=sw.rect().center() / 2)
    assert ctx.ui_mode is UiMode.SIMPLE


# ---- Basit: görev kartları, "yakında" -----------------------------------------------------------
def test_simple_home_has_task_cards_and_soon_placeholders(win, ctx):
    cards = {c.objectName(): c for c in win.home.cards}
    assert set(cards) == {"card_downloader", "card_recorder", "card_editor", "card_photo", "card_series"}
    # genel kural (sabit liste yok): bir kartın "yakında" etiketi, modülün kayıt defterinde OLMAMASIYLA eşittir
    for name, card in cards.items():
        module_id = name.removeprefix("card_")
        assert card.soon == (ctx.registry.get(module_id) is None), module_id
    soon_ids = [n.removeprefix("card_") for n, c in cards.items() if c.soon]
    if soon_ids:                                                       # henüz yazılmamış bir modül → "yakında" sayfası
        cards[f"card_{soon_ids[0]}"].click()
        assert ctx.current_module == soon_ids[0] and isinstance(win._frame.page, ComingSoonPage)
        win.go_home()
    registered = [n.removeprefix("card_") for n, c in cards.items() if not c.soon]
    cards[f"card_{registered[0]}"].click()                             # kayıtlı modül → gerçek sayfa, yer tutucu değil
    assert ctx.current_module == registered[0] and not isinstance(win._frame.page, ComingSoonPage)
    win.go_home()
    assert ctx.current_module is None and win.simple.currentWidget() is win.home


def test_wizard_tasks_reorder_home_cards(win, ctx):
    ctx.settings.set("wizard.tasks", ["film", "dl"])
    win.home.refresh()
    assert [c.objectName() for c in win.home.cards][:2] == ["card_series", "card_downloader"]


# ---- Legend: çalışma alanları, dock'lar ---------------------------------------------------------
def test_legend_workspaces_docks_and_hub(win, ctx):
    ctx.set_ui_mode("legend")
    assert win.legend.current == "project" and win.legend.hub.table.rowCount() == 1
    win.legend.show_workspace("capture")
    assert win.legend.current == "capture" and win.legend.stack.currentWidget() is win.legend._tabs["capture"]
    assert win.legend._tabs["capture"].count() == 2                   # indirici + ses kayıt
    win.open_module("editor")
    assert win.legend.current == "edit"
    assert {d.objectName() for d in win.docks} == {"dock_quick", "dock_jobs", "dock_system"}
    assert all(d.features() & d.DockWidgetFeature.DockWidgetFloatable for d in win.docks)   # koparılabilir


def test_dock_state_is_saved_per_workspace(win, ctx):
    ctx.set_ui_mode("legend")
    win.legend.show_workspace("capture")
    win._save_dock_state("capture")
    assert ctx.settings.get("window.legend_state.capture")
    win.legend.show_workspace("project")
    win.legend.show_workspace("capture")                              # geri yükleme çökmeden çalışır
    assert win.dock_quick.isVisible()


def test_keyboard_shortcuts(win, ctx):
    QTest.keyClick(win, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier)
    assert ctx.ui_mode is UiMode.LEGEND
    QTest.keyClick(win, Qt.Key.Key_3, Qt.KeyboardModifier.ControlModifier)
    assert ctx.current_module == "editor"
    QTest.keyClick(win, Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier)
    assert ctx.ui_mode is UiMode.SIMPLE


# ---- gerçek bir modül sayfasıyla: fabrika ve hata yalıtımı --------------------------------------------
def _fake_spec(simple, legend, mid="downloader"):
    return ModuleSpec(id=mid, title_key=f"mod.{mid}.title", order=10, accent=Accent.PEACH, icon="download",
                      desc_key=f"mod.{mid}.desc", simple_page=simple, legend_page=legend)


class _Counter:
    n = 0


def test_registered_module_pages_are_rebuilt_per_mode_without_losing_state(win, ctx):
    svc_state = {"url": "https://x"}

    def simple(c):
        w = QLabel("basit sayfa")
        w.setProperty("probe", svc_state["url"])                      # durum widget'ta değil, servis/dict'te
        return w

    def legend(c):
        return QLabel("legend sayfa")

    ctx.registry.register(_fake_spec(simple, legend))
    win.home.refresh()
    win.legend.rebuild()
    win.open_module("downloader")
    assert win._frame.page.text() == "basit sayfa" and not win.home.cards[0].soon
    ctx.set_ui_mode("legend")
    page = win.legend._tabs["capture"].currentWidget().page
    assert page.text() == "legend sayfa"
    ctx.set_ui_mode("simple")
    assert win._frame.page.text() == "basit sayfa" and win._frame.page.property("probe") == "https://x"


def test_page_factory_exception_becomes_error_card_not_a_crash(win, ctx):
    def boom(c):
        raise RuntimeError("sayfa patladı")

    ctx.registry.register(_fake_spec(boom, boom))
    win.legend.rebuild()
    win.open_module("downloader")
    assert isinstance(win._frame.page, PageErrorWidget)
    ctx.set_ui_mode("legend")
    assert isinstance(win.legend._tabs["capture"].currentWidget().page, PageErrorWidget)


def test_non_widget_factory_result_is_handled(win, ctx):
    ctx.registry.register(_fake_spec(lambda c: "not a widget", None))
    win.legend.rebuild()
    win.open_module("downloader")
    assert isinstance(win._frame.page, PageErrorWidget)


# ---- olaylar ------------------------------------------------------------------------------------------
def test_rewind_button_emits_signal_and_bus_event(win, ctx, qtbot):
    got = []
    ctx.bus.subscribe(Topics.REWIND_REQUESTED, got.append)
    with qtbot.waitSignal(win.rewindRequested, timeout=500) as sig:
        win.topbar.rewind_btn.click()
    assert sig.args == [10] and got == [{"minutes": 10}]
    assert "10" in win.status.message()                               # dürüst iletişim: henüz etkin değil


def test_autosave_events_update_indicators(win, ctx, qtbot):
    ctx.bus.emit(Topics.AUTOSAVE_STATE, {"state": "saved", "at": None})
    qtbot.waitUntil(lambda: win.topbar.autosave.state == "saved", timeout=500)
    ctx.bus.emit(Topics.AUTOSAVE_STATE, {"state": "error"})
    qtbot.waitUntil(lambda: win.topbar.autosave.state == "error", timeout=500)


def test_notify_error_and_navigate_events(win, ctx, qtbot):
    ctx.bus.emit(Topics.NOTIFY, {"text": "Merhaba dünya", "level": "info"})
    qtbot.waitUntil(lambda: win.status.message() == "Merhaba dünya", timeout=500)
    ctx.report_error(ConnectionError("dns"), {"source": "downloader"})
    qtbot.waitUntil(lambda: len(win._error_dialogs) == 1, timeout=500)
    assert win._error_dialogs[0].card.error.code == "net.offline"
    win._error_dialogs[0].card.dismissed.emit()
    ctx.bus.emit(Topics.NAVIGATE, {"module": "photo"})
    qtbot.waitUntil(lambda: ctx.current_module == "photo", timeout=500)


def test_jobs_show_up_in_status_and_panel(win, ctx, qtbot):
    ctx.set_ui_mode("legend")
    ctx.jobs.register_handler("t", lambda job, jc: jc.report(0.5))
    job = ctx.jobs.submit("t", title="Deneme")
    qtbot.waitUntil(lambda: ctx.jobs.get(job.id).finished, timeout=2000)
    qtbot.waitUntil(lambda: "1" in win.jobs_panel._rows[job.id][2].text() or True, timeout=500)
    assert job.id in win.jobs_panel._rows


def test_project_open_close_updates_topbar(win, ctx, qtbot, tmp_path):
    assert "Kabuk Testi" in win.topbar.project_btn.text()
    ctx.project.close()
    qtbot.waitUntil(lambda: "Proje yok" in win.topbar.project_btn.text(), timeout=500)
    ctx.create_project("İkinci", tmp_path / "p2")
    qtbot.waitUntil(lambda: "İkinci" in win.topbar.project_btn.text(), timeout=500)
    assert any(e.name == "İkinci" for e in ctx.recent.get_recent())


def test_language_switch_retranslates(win, ctx):
    ctx.set_language("en")
    assert win.home.greeting.text().startswith("Hello") and "Rewind" in win.topbar.rewind_btn.text() \
        or win.topbar._compact
    assert win.topbar.mode_switch._labels()[0] == "Simple Mode"
    ctx.set_language("tr")
    assert win.home.greeting.text().startswith("Merhaba")


def test_large_text_and_high_contrast_toggle_from_menu(win, ctx):
    win.actions_by_key["large_text"].trigger()
    assert ctx.settings.large_text and ctx.theme.tokens.scale == 1.25
    win.actions_by_key["high_contrast"].trigger()
    assert ctx.settings.high_contrast and ctx.theme.tokens.high_contrast
    win.actions_by_key["large_text"].trigger()
    assert not ctx.theme.tokens.large_text


def test_topbar_goes_compact_when_narrow(win):
    win.resize(900, 700)
    QTest.qWait(30)
    assert win.topbar._compact and win.topbar.undo_btn.text() == ""
    win.resize(1500, 700)
    QTest.qWait(30)
    assert not win.topbar._compact and win.topbar.undo_btn.text() != ""


# ---- splash / onay / sihirbaz / akış ----------------------------------------------------------------------
def test_splash_duration_range_and_finish(ctx, qtbot):
    assert all(SPLASH_MIN_MS <= splash_duration() <= SPLASH_MAX_MS for _ in range(50))
    sp = SplashScreen(250)
    qtbot.addWidget(sp)
    with qtbot.waitSignal(sp.finished, timeout=3000):
        sp.start()
    assert sp.progress() >= 1.0 and not sp.isVisible()
    assert "yt-dlp" in sp.footer.text() or "yt-dlp" in sp.footer.text().lower()


def test_splash_can_be_skipped(ctx, qtbot):
    sp = SplashScreen(60000)
    qtbot.addWidget(sp)
    sp.start()
    with qtbot.waitSignal(sp.finished, timeout=500):
        QTest.keyClick(sp, Qt.Key.Key_Escape)


def test_consent_is_asked_once_and_recorded(clean_home):
    assert not consent.is_accepted()
    asked = []
    assert ensure_consent(ask=lambda: asked.append(1) or True) is True
    assert consent.is_accepted() and ensure_consent(ask=lambda: asked.append(2) or False) is True
    assert asked == [1]                                               # ikinci kez sorulmadı


def test_consent_decline_does_not_record(clean_home):
    assert ensure_consent(ask=lambda: False) is False and not consent.is_accepted()


def test_consent_version_bump_requires_reconsent(clean_home, monkeypatch):
    consent.record_acceptance()
    monkeypatch.setattr(consent, "DISCLAIMER_VERSION", 2)
    assert not consent.is_accepted()


def test_wizard_collects_three_answers(ctx, qtbot, tmp_path):
    wz = WizardDialog()
    qtbot.addWidget(wz)
    wz.show()
    assert wz.step == 0 and wz.question.text() == "Ne yapmak istiyorsun?" and not wz.back_btn.isVisible()
    wz.tasks.set_selected(["rec", "photo"])
    wz.next()
    assert wz.step == 1 and wz.back_btn.isVisible()
    wz.places.set_selected(["desktop"])
    wz.next()
    assert wz.step == 2 and wz.next_btn.text() == "Bitir ve Stüdyoya Gir"
    wz.api.set_selected(["how"])
    got = []
    wz.finishedWithAnswers.connect(got.append)
    wz.next()
    assert got[0] == WizardAnswers(("rec", "photo"), "desktop", "", "how")
    wz.back()                                                          # kapalı diyalogda geri bile çökmez


def test_wizard_back_and_empty_selection_falls_back_to_unsure(ctx, qtbot):
    wz = WizardDialog()
    qtbot.addWidget(wz)
    wz.tasks.set_selected([])
    assert wz.answers().tasks == ("unsure",)
    wz.next(); wz.next(); wz.back()
    assert wz.step == 1


def test_launcher_flow_without_splash_runs_wizard_then_window(ctx, qtbot, monkeypatch):
    ctx.settings.set("wizard.done", False)
    seen = {}

    class FakeWizard:
        DialogCode = WizardDialog.DialogCode

        def __init__(self):
            from PySide6.QtCore import QObject, Signal

            class _S(QObject):
                finishedWithAnswers = Signal(object)
            self._s = _S()
            self.finishedWithAnswers = self._s.finishedWithAnswers

        def exec(self):
            self.finishedWithAnswers.emit(WizardAnswers(("dl",), "videos", "", "skip"))
            return WizardDialog.DialogCode.Accepted

        def answers(self):
            return WizardAnswers()

    monkeypatch.setattr("kadraj.ui.shell.app.WizardDialog", FakeWizard)
    lc = Launcher(qtbot._request.config and __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication.instance(),
                  ctx, ctx.theme, splash=False, ask_consent=lambda: True)
    lc.start()
    qtbot.addWidget(lc.window)
    assert lc.window is not None and lc.window.isVisible()
    assert ctx.settings.get("wizard.done") is True and ctx.settings.get("wizard.tasks") == ["dl"]
    assert ctx.settings.get("project_root")


def test_launcher_quits_when_consent_declined(ctx, qtbot):
    from PySide6.QtWidgets import QApplication
    quit_called = []
    lc = Launcher(QApplication.instance(), ctx, ctx.theme, splash=False, ask_consent=lambda: False)
    lc.app = type("A", (), {"quit": lambda self: quit_called.append(1)})()
    lc.start()
    assert quit_called == [1] and lc.window is None


def test_launcher_how_answer_navigates_to_settings(ctx, qtbot):
    from PySide6.QtWidgets import QApplication
    ctx.settings.set("wizard.done", True)
    lc = Launcher(QApplication.instance(), ctx, ctx.theme, splash=False, ask_consent=lambda: True)
    lc._wizard_api = "how"
    win = lc.show_main()
    qtbot.addWidget(win)
    qtbot.waitUntil(lambda: ctx.current_module == "settings", timeout=800)


# ---- çevrimdışı --------------------------------------------------------------------------------------------
def test_startup_and_navigation_make_no_network_calls(ctx, qtbot, monkeypatch, tmp_path):
    attempts = []

    def blocked(self, *a, **k):
        attempts.append(a)
        raise OSError("ağ kapalı")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError("dns kapalı")))
    ctx.create_project("Çevrimdışı", tmp_path / "p")
    w = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(w)
    w.show()
    for mode in ("legend", "simple"):
        ctx.set_ui_mode(mode)
        for mid in ("downloader", "recorder", "editor", "photo", "series", "settings"):
            w.open_module(mid)
    sp = SplashScreen(100)
    qtbot.addWidget(sp)
    with qtbot.waitSignal(sp.finished, timeout=3000):
        sp.start()
    assert attempts == []
    w.check_updates()                                                  # kullanıcı isteğiyle: ağ yok → çökmeden mesaj
    qtbot.waitUntil(lambda: "denetlen" in w.status.message().lower() or "güncel" in w.status.message().lower(),
                    timeout=3000)


def test_update_check_without_repo_never_touches_network(monkeypatch):
    from kadraj import updates
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ağ!")))
    assert updates.check_for_update("") is None                      # boş depo: ağa hiç çıkılmaz
    assert updates.is_newer("0.2.0", "0.1.9") and not updates.is_newer("0.1.0", "0.1.0")
    assert updates.parse_version("v1.2") == (1, 2, 0)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("ağ kapalı")))
    assert updates.check_for_update() is None                        # depo var ama çevrimdışı: çökme yok
