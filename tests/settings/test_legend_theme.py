"""Ayarlar sayfalarının temayla boyanması: Legend koyu (sol menü dahil), Basit açık; piksel ölçümü (ağsız)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QListWidget

from kadraj.core.contracts import UiMode
from kadraj.modules.settings.page_legend import SettingsLegendPage
from kadraj.modules.settings.page_simple import SettingsSimplePage
from kadraj.ui.shell.main_window import MainWindow
from kadraj.ui.shell.pages import build_module_page
from kadraj.ui.theme.tokens import resolve
from tests.ui_pixels import (background_color, color_at, ink_contrast, luminance, render, to_page,
                             widget_rect_in)


def _entry(ctx):
    return next(e for e in ctx.registry.entries() if e.id == "settings")


def _item_rect_in_page(page, lst: QListWidget, row: int) -> QRect:
    item_rect = lst.visualItemRect(lst.item(row))
    return QRect(to_page(lst.viewport(), page, item_rect.topLeft()), item_rect.size())


def test_legend_settings_is_dark_including_the_section_menu(ctx, svc, qtbot):
    ctx.set_ui_mode("legend")
    page = build_module_page(ctx, _entry(ctx), UiMode.LEGEND)
    qtbot.addWidget(page)
    assert isinstance(page, SettingsLegendPage)
    tk = resolve("legend")
    image = render(page)
    bg = background_color(image, page)
    assert bg.name() == QColor(tk.bg).name()
    nav = page.nav
    body = color_at(image, page, nav.viewport(), QPoint(nav.viewport().width() // 2, nav.viewport().height() - 6))
    assert body.name() == QColor(tk.card).name()             # sol menü: beyaz değil, kart tonu
    assert luminance(body) < 0.05
    assert ink_contrast(image, _item_rect_in_page(page, nav, 1)) >= 4.5   # bölüm adları okunur
    assert ink_contrast(image, widget_rect_in(page, page.cb_large)) >= 4.5 # görünüm seçenekleri okunur


def test_basit_settings_is_light_with_readable_text(ctx, svc, qtbot):
    ctx.set_ui_mode("simple")
    page = build_module_page(ctx, _entry(ctx), UiMode.SIMPLE)
    qtbot.addWidget(page)
    assert isinstance(page, SettingsSimplePage)
    tk = resolve("simple")
    image = render(page)
    bg = background_color(image, page)
    assert bg.name() == QColor(tk.bg).name()
    assert luminance(bg) > 0.7
    title = next(label for label in page.findChildren(QLabel) if label.property("role") == "title")
    assert ink_contrast(image, widget_rect_in(page, title)) >= 4.5


def test_legend_settings_inside_the_main_window_is_dark(ctx, svc, qtbot, tmp_path):
    ctx.create_project("Tema Testi", tmp_path / "projeler")
    ctx.set_ui_mode("legend")
    window = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(window)
    window.resize(1320, 860)
    window.show()
    window.open_module("settings")
    page = window.findChild(SettingsLegendPage)
    assert page is not None, "Ayarlar Legend sayfası kabukta açılmadı"
    tk = resolve("legend")
    image = window.grab().toImage()
    bg = background_color(image, page, host=window)
    assert bg.name() == QColor(tk.bg).name()
    nav = page.nav
    body = image.pixelColor(to_page(nav.viewport(), window, QPoint(nav.viewport().width() // 2,
                                                                  nav.viewport().height() - 6)))
    assert luminance(body) < 0.05


def test_basit_settings_inside_the_main_window_is_light(ctx, svc, qtbot, tmp_path):
    from kadraj.modules.settings.page_simple import SettingsSimplePage as Basit

    ctx.create_project("Tema Testi", tmp_path / "projeler")
    ctx.set_ui_mode("simple")
    window = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(window)
    window.resize(1320, 860)
    window.show()
    window.open_module("settings")
    page = window.findChild(Basit)
    assert page is not None, "Ayarlar Basit sayfası kabukta açılmadı"
    tk = resolve("simple")
    image = window.grab().toImage()
    bg = background_color(image, page, host=window)
    assert bg.name() == QColor(tk.bg).name()
    assert luminance(bg) > 0.7
