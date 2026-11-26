"""Kayıt yeri görünür: kayıt öncesi tam yol + "Klasörü aç"/"Değiştir"; kayıt sonrası sonuç kartı; son kayıtlar listesi.

Dosya eylemleri (oynat, klasörde göster) gerçek oynatıcı/dosya yöneticisi açmadan, sahte ``QDesktopServices`` ile denenir.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from kadraj.modules.recorder import location as loc
from kadraj.modules.recorder.takes import TAKE_PREFIX


class FakeDesktop:
    urls: list = []
    answer = True

    @classmethod
    def openUrl(cls, url):
        cls.urls.append(url.toLocalFile())
        return cls.answer


@pytest.fixture(autouse=True)
def _fake_desktop(monkeypatch):
    FakeDesktop.urls = []
    FakeDesktop.answer = True
    monkeypatch.setattr(loc, "QDesktopServices", FakeDesktop)
    yield


@pytest.fixture()
def simple(ctx, svc, qtbot):
    ctx.set_ui_mode("simple")
    page = ctx.registry.get("recorder").simple_page(ctx)
    qtbot.addWidget(page)
    page.resize(1300, 900)
    page.show()
    return page


@pytest.fixture()
def legend(ctx, svc, qtbot):
    ctx.set_ui_mode("legend")
    page = ctx.registry.get("recorder").legend_page(ctx)
    qtbot.addWidget(page)
    page.resize(1100, 800)
    page.show()
    return page


def _texts(widget) -> str:
    return " ".join(label.text() for label in widget.findChildren(QLabel))


def _button(widget, text: str) -> QPushButton:
    for b in widget.findChildren(QPushButton):
        if b.text() == text:
            return b
    raise AssertionError(f"düğme yok: {text!r}")


def _record_one_take(svc, page):
    QTest.mouseClick(page.record_btn, Qt.MouseButton.LeftButton)
    svc.mic.feed(1.5, amp=0.3, modulate=False)
    svc.engine.flush()
    svc._tick()
    QTest.mouseClick(page.record_btn, Qt.MouseButton.LeftButton)
    takes = svc.takes()
    assert takes, "kayıt listesi boş"
    return takes[0]


# ----------------------------------------------------------------------------------------------- kayıt öncesi
def test_simple_shows_full_recording_path_before_recording(svc, simple):
    folder = svc.output_folder()
    assert simple.location.folder == folder
    assert f"Kayıt yeri: {folder}" in simple.location.label.text()
    assert simple.result_card.isHidden()
    assert _button(simple, "Klasörü aç") and _button(simple, "Değiştir")


def test_legend_shows_full_recording_path_before_recording(svc, legend):
    folder = svc.output_folder()
    assert f"Kayıt yeri: {folder}" in legend.location.label.text()
    assert legend.result_card.isHidden()
    assert _button(legend, "Klasörü aç") and _button(legend, "Değiştir")


def test_open_folder_button_opens_the_recording_folder(svc, simple):
    _button(simple, "Klasörü aç").click()
    assert [Path(u) for u in FakeDesktop.urls] == [svc.output_folder()]          # Qt '/' verir; Windows'ta '\\' ile eşit sayılsın
    assert simple.location.note.isHidden()


def test_open_folder_failure_shows_the_full_path_to_copy(svc, simple):
    FakeDesktop.answer = False
    _button(simple, "Klasörü aç").click()
    assert str(svc.output_folder()) in simple.location.note.text()
    assert not simple.location.note.isHidden()


def test_change_button_sends_the_user_to_settings(ctx, svc, simple, monkeypatch):
    sent = []
    monkeypatch.setattr(ctx.bus, "emit", lambda topic, payload=None: sent.append((topic, payload)))
    _button(simple, "Değiştir").click()
    assert sent == [("ui.navigate", {"module": "settings"})]


# ----------------------------------------------------------------------------------------------- kayıt sonrası
def test_simple_result_card_shows_name_duration_and_full_path(ctx, svc, simple):
    take = _record_one_take(svc, simple)
    card = simple.result_card
    assert not card.isHidden() and card.take == take
    assert card.name.text() == take.path.name and take.path.name.startswith(TAKE_PREFIX)
    assert "0:01" in card.meta.text() or "0:02" in card.meta.text()        # 1.5 sn → süre
    assert str(take.path) in card.where.text()
    assert _button(card, "Oynat") and _button(card, "Klasörde göster")


def test_legend_result_card_appears_after_stop(ctx, svc, legend):
    take = _record_one_take(svc, legend)
    assert not legend.result_card.isHidden()
    assert str(take.path) in legend.result_card.where.text()


def test_play_and_show_in_folder_use_the_saved_file(ctx, svc, simple):
    take = _record_one_take(svc, simple)
    _button(simple.result_card, "Oynat").click()
    _button(simple.result_card, "Klasörde göster").click()
    assert [Path(u) for u in FakeDesktop.urls] == [take.path, take.path.parent]


def test_play_failure_is_explained_on_the_card(ctx, svc, simple):
    take = _record_one_take(svc, simple)
    FakeDesktop.answer = False
    _button(simple.result_card, "Oynat").click()
    assert "Oynatıcı açılamadı" in simple.result_card.note.text()
    assert take.path.is_file()


def test_starting_a_new_recording_hides_the_old_result(ctx, svc, simple):
    _record_one_take(svc, simple)
    assert not simple.result_card.isHidden()
    QTest.mouseClick(simple.record_btn, Qt.MouseButton.LeftButton)
    assert simple.result_card.isHidden()
    svc.stop_recording()


# ----------------------------------------------------------------------------------------------- son kayıtlar
def test_recent_recordings_list_is_titled_and_shows_the_take(ctx, svc, simple):
    take = _record_one_take(svc, simple)
    assert "Son kayıtlar" in _texts(simple)
    assert any(row.take.path == take.path for row in simple.rows)


def test_legend_recent_recordings_panel_has_the_take_in_its_table(ctx, svc, legend):
    _record_one_take(svc, legend)
    assert "SON KAYITLAR" in _texts(legend)
    assert legend.table.rowCount() == 1
