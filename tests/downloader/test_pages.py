"""Sayfalar: kabuk üzerinden iki modda açılır, çözümleme sonrası görünüm, taslak paylaşımı, hata kartı."""
from __future__ import annotations

import urllib.error

from kadraj.core.contracts import UiMode
from kadraj.core.registry import ModuleRegistry
from kadraj.modules.downloader.page_legend import DownloaderLegendPage
from kadraj.modules.downloader.page_simple import DownloaderSimplePage
from kadraj.ui.shell.pages import build_module_page

from .fakes import FakeYDL

URL = "https://www.youtube.com/watch?v=abc123"


def _entry():
    reg = ModuleRegistry()
    reg.discover()
    return next(e for e in reg.entries() if e.id == "downloader")


def test_both_pages_open_through_the_shell_in_both_modes(ctx, qtbot):
    entry = _entry()
    simple = build_module_page(ctx, entry, UiMode.SIMPLE)
    legend = build_module_page(ctx, entry, UiMode.LEGEND)
    qtbot.addWidget(simple)
    qtbot.addWidget(legend)
    assert isinstance(simple, DownloaderSimplePage)
    assert isinstance(legend, DownloaderLegendPage)


def test_simple_page_shows_found_video_and_enables_download(ctx, svc, qtbot):
    page = DownloaderSimplePage(ctx)
    qtbot.addWidget(page)
    assert not page.go.isEnabled()
    page.url.setText(URL)
    page.url.returnPressed.emit()
    qtbot.waitUntil(lambda: page.go.isEnabled(), timeout=5000)
    assert "YouTube" in page.chip.text() and "3 dk 45 sn" in page.chip.text()
    assert page.range.duration() == 225.0
    assert "MB" in page.quality_cards[1]._trailing          # 1080p boyut tahmini
    assert page.range_text.text() == "Tüm video"


def test_partial_range_text_and_draft(ctx, svc, qtbot):
    page = DownloaderSimplePage(ctx)
    qtbot.addWidget(page)
    page.url.setText(URL)
    page.url.returnPressed.emit()
    qtbot.waitUntil(lambda: page.go.isEnabled(), timeout=5000)
    page.partial.setChecked(True)
    page.range.set_values(80.0, 225.0)
    page.range.valuesChanged.emit(80.0, 225.0)
    assert svc.draft.partial and svc.draft.start == 80.0
    assert page.range_text.text() == "1:20'den başla · 2 dk 25 sn seçtin · 3:45'te bitir"


def test_mode_choice_updates_the_shared_draft(ctx, svc, qtbot):
    page = DownloaderSimplePage(ctx)
    qtbot.addWidget(page)
    page.mode_cards[1].click()
    assert svc.draft.mode == "audio"


def test_draft_is_shared_when_switching_to_legend(ctx, svc, qtbot):
    simple = DownloaderSimplePage(ctx)
    qtbot.addWidget(simple)
    simple.mode_cards[2].click()                             # Sadece Görüntü
    legend = DownloaderLegendPage(ctx)
    qtbot.addWidget(legend)
    assert legend.mode.currentData() == "video"


def test_legend_page_lists_formats_and_builds_command(ctx, svc, qtbot):
    page = DownloaderLegendPage(ctx)
    qtbot.addWidget(page)
    page.url.setText(URL)
    page.list_btn.click()
    qtbot.waitUntil(lambda: page.formats.rowCount() == 4, timeout=5000)
    assert "YouTube" in page.meta.text()
    page.formats.selectRow(1)                                # 137 (1080p, yalnız görüntü)
    assert svc.draft.format_ids == ("137",)
    assert "137" in page.command.text()
    page.t_in.setText("00:01:20")
    page.t_out.setText("00:03:45")
    page._on_time_edit()
    assert svc.draft.partial and (svc.draft.start, svc.draft.end) == (80.0, 225.0)
    assert "--download-sections" in page.command.text()


def test_offline_probe_shows_error_card_and_keeps_page_alive(ctx, svc, qtbot):
    FakeYDL.scenario["error"] = urllib.error.URLError(OSError("ağ yok"))
    page = DownloaderSimplePage(ctx)
    qtbot.addWidget(page)
    page.url.setText(URL)
    page.url.returnPressed.emit()
    qtbot.waitUntil(lambda: page.error_box.count() == 1, timeout=5000)
    assert not page.go.isEnabled()


def test_download_button_queues_a_job_and_lists_it(ctx, svc, qtbot, tmp_path):
    ctx.settings.set("project_root", str(tmp_path))
    page = DownloaderSimplePage(ctx)
    qtbot.addWidget(page)
    page.url.setText(URL)
    page.url.returnPressed.emit()
    qtbot.waitUntil(lambda: page.go.isEnabled(), timeout=5000)
    page.go.click()
    assert page.status.text().startswith("Kuyruğa eklendi")
    assert ctx.jobs.wait_idle(10000)
    assert any(r["path"] for r in svc.recent())
    assert list((tmp_path / "01_Downloads").glob("*.mp4"))
