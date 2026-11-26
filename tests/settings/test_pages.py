"""Sayfalar: kabuk üzerinden iki modda açılır; kasa satırları, göz düğmesi, yerel satırlar, Legend bölümleri."""
from __future__ import annotations

from kadraj.core.contracts import UiMode
from kadraj.ui.shell.pages import build_module_page
from kadraj.modules.settings.page_legend import SettingsLegendPage
from kadraj.modules.settings.page_simple import SettingsSimplePage

from .fakes import FULL_KEY


def _entry(ctx):
    return next(e for e in ctx.registry.entries() if e.id == "settings")


def _wait_local(page, qtbot):
    qtbot.waitUntil(lambda: len(page.service.local_runtimes()) == 3, timeout=5000)


def test_both_pages_open_through_the_shell_in_both_modes(ctx, svc, qtbot):
    entry = _entry(ctx)
    simple = build_module_page(ctx, entry, UiMode.SIMPLE)
    legend = build_module_page(ctx, entry, UiMode.LEGEND)
    qtbot.addWidget(simple)
    qtbot.addWidget(legend)
    assert isinstance(simple, SettingsSimplePage) and isinstance(legend, SettingsLegendPage)


def test_simple_page_key_rows_and_masked_eye(ctx, svc, qtbot):
    page = SettingsSimplePage(ctx)
    qtbot.addWidget(page)
    assert page.key_box.count() == 9
    def pills():
        return [w.text() for w in page.findChildren(type(page.status)) if w.property("role") == "pill"]

    assert "sk-••••1234" in pills()                      # varsayılan: kasanın maskesi
    page._toggle_reveal("anthropic")                     # göz: yalnızca ilk 6 + … + son 4
    assert "sk-ant…1234" in pills()
    assert all(FULL_KEY not in t for t in pills())


def test_simple_mode_cards_switch_the_ui_mode(ctx, svc, qtbot):
    page = SettingsSimplePage(ctx)
    qtbot.addWidget(page)
    page.mode_cards["legend"].mousePressEvent(_press())
    assert ctx.ui_mode is UiMode.LEGEND
    page.mode_cards["simple"].mousePressEvent(_press())
    assert ctx.ui_mode is UiMode.SIMPLE


def test_simple_page_shows_local_runtimes(ctx, svc, qtbot):
    page = SettingsSimplePage(ctx)
    qtbot.addWidget(page)
    svc.refresh_local(background=False)
    assert page.local_box.count() == 3


def test_simple_view_checkboxes_reflect_and_save(ctx, svc, qtbot):
    page = SettingsSimplePage(ctx)
    qtbot.addWidget(page)
    page.cb_large.setChecked(True)
    assert svc.a11y("large_text") is True
    page.cb_tips.setChecked(False)
    assert svc.tips_enabled() is False


def test_legend_page_tables_and_sections(ctx, svc, qtbot):
    page = SettingsLegendPage(ctx)
    qtbot.addWidget(page)
    assert page.nav.count() == 7 and page.stack.count() == 7
    for row in range(7):                                  # bölümler ilk seçildiğinde kurulur
        page.nav.setCurrentRow(row)
    assert page.api_table.rowCount() == 9
    svc.refresh_local(background=False)
    page._refresh_local()
    assert page.local_table.rowCount() == 3
    assert page.kind_combo.count() == 6
    assert page.profile_table.rowCount() >= 1
    assert page.path_labels["project"].text()


def test_legend_guide_drawer_opens_for_a_provider(ctx, svc, qtbot):
    page = SettingsLegendPage(ctx)
    qtbot.addWidget(page)
    page.show()
    page.nav.setCurrentRow(1)                             # API bölümü (rehber çekmecesi orada)
    page._open_drawer("pexels")
    assert page.guide.provider_id() == "pexels"
    assert not page.drawer.isHidden()


def test_legend_mode_radio_follows_the_ui_mode(ctx, svc, qtbot):
    page = SettingsLegendPage(ctx)
    qtbot.addWidget(page)
    svc.set_ui_mode("legend")
    assert page.mode_radios["legend"].isChecked()
    svc.set_ui_mode("simple")


def _press():
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    return QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(1, 1), QPointF(1, 1), Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


def test_tools_show_each_components_source_and_onar_runs_a_seeded_repair(ctx, svc, qtbot):
    from PySide6.QtWidgets import QAbstractButton, QLabel

    from kadraj.core.updater import Report
    from kadraj.modules.settings.service import ToolInfo

    seen = []

    class _Runner:
        def run(self, progress=None, *, force=False, **_kw):
            return Report(current=["yt-dlp"])

    def factory(seed=False):
        seen.append(seed)
        return _Runner()

    svc.updater_factory = factory
    svc._tools = ToolInfo("2025.1", "7.1", "/opt/ffmpeg", "2.0",
                          {"yt-dlp": "updated", "ffmpeg": "bundled", "deno": "system"})
    svc._tools_loaded = True

    simple = SettingsSimplePage(ctx)
    qtbot.addWidget(simple)
    simple._refresh_tools()
    texts = " ".join(label.text() for label in simple.findChildren(QLabel))
    assert "Video indirici (yt-dlp): 2025.1 · Güncellendi" in texts
    assert "Ses ve video motoru (FFmpeg): 7.1 · Pakette" in texts
    assert "Video çözücü (Deno): 2.0 · Sistemde" in texts
    assert not [b for b in simple.findChildren(QAbstractButton) if "kurarım" in b.text()]

    legend = SettingsLegendPage(ctx)
    qtbot.addWidget(legend)
    legend._show_section(5)                                  # "Araçlar" bölümü (TOPIC_SECTION["tools"] == 5)
    assert [legend.tools_table.item(r, 2).text() for r in range(3)] == ["Güncellendi", "Pakette", "Sistemde"]
    legend_repair = [b for b in legend.findChildren(QAbstractButton) if b.text() == "Onar"]
    assert len(legend_repair) == 1
    legend_repair[0].click()
    assert ctx.jobs.wait_idle(10000)
    assert seen == [True]

    simple_repair = [b for b in simple.findChildren(QAbstractButton) if b.text() == "Onar"]
    assert len(simple_repair) == 1
    simple_repair[0].click()
    assert ctx.jobs.wait_idle(10000)
    assert seen == [True, True]
