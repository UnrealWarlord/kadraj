"""İndirici sayfalarının temayla boyanması: Legend koyu, Basit açık; piksel ölçümü (ağsız, ekransız)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QTableWidget

from kadraj.core.contracts import UiMode
from kadraj.modules.downloader.page_legend import DownloaderLegendPage
from kadraj.modules.downloader.page_simple import DownloaderSimplePage
from kadraj.ui.shell.main_window import MainWindow
from kadraj.ui.shell.pages import build_module_page
from kadraj.ui.theme.tokens import resolve
from tests.ui_pixels import (background_color, color_at, ink_contrast, luminance, render, table_body_point,
                             to_page, widget_rect_in)


def _entry(ctx):
    return next(e for e in ctx.registry.entries() if e.id == "downloader")


def _heading(page):
    labels = [label for label in page.findChildren(QLabel) if label.property("role") == "h2" and label.text()]
    assert labels, "başlık etiketi bulunamadı"
    return labels[0]


def test_legend_page_is_dark_and_tables_follow_the_card_tone(ctx, qtbot):
    ctx.set_ui_mode("legend")
    page = build_module_page(ctx, _entry(ctx), UiMode.LEGEND)
    qtbot.addWidget(page)
    assert isinstance(page, DownloaderLegendPage)
    tk = resolve("legend")
    image = render(page)
    bg = background_color(image, page)
    assert bg.name() == QColor(tk.bg).name()
    assert luminance(bg) < 0.05
    tables = page.findChildren(QTableWidget)
    assert len(tables) == 2                                   # biçim tablosu + indirme kuyruğu
    for table in tables:
        body = color_at(image, page, table.viewport(), table_body_point(table))
        assert body.name() == QColor(tk.card).name()
        assert luminance(body) < 0.05
    assert ink_contrast(image, widget_rect_in(page, _heading(page))) >= 4.5


def test_basit_page_is_light_with_readable_text(ctx, qtbot):
    ctx.set_ui_mode("simple")
    page = build_module_page(ctx, _entry(ctx), UiMode.SIMPLE)
    qtbot.addWidget(page)
    assert isinstance(page, DownloaderSimplePage)
    tk = resolve("simple")
    image = render(page)
    bg = background_color(image, page)
    assert bg.name() == QColor(tk.bg).name()
    assert luminance(bg) > 0.7
    assert ink_contrast(image, widget_rect_in(page, _heading(page))) >= 4.5


def test_legend_page_inside_the_main_window_is_dark(ctx, qtbot, tmp_path):
    ctx.create_project("Tema Testi", tmp_path / "projeler")
    ctx.set_ui_mode("legend")
    window = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(window)
    window.resize(1320, 860)
    window.show()
    window.open_module("downloader")
    page = window.findChild(DownloaderLegendPage)
    assert page is not None, "Legend sayfası kabukta açılmadı"
    tk = resolve("legend")
    image = window.grab().toImage()
    bg = background_color(image, page, host=window)
    assert bg.name() == QColor(tk.bg).name()
    for table in page.findChildren(QTableWidget):
        body = image.pixelColor(to_page(table.viewport(), window, table_body_point(table)))
        assert luminance(body) < 0.05
