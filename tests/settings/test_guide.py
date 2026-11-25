"""Rehber: adım gezintisi, sağlayıcı seçimi, adres kopyalama, QPainter çizimi."""
from __future__ import annotations

from PySide6.QtWidgets import QApplication

from kadraj.modules.settings import guide as G


def _view(qtbot, providers=None):
    view = G.GuideView(providers or {"anthropic": "Anthropic (Claude)", "pexels": "Pexels"})
    qtbot.addWidget(view)
    view.resize(560, 420)
    return view


def test_five_steps_with_back_and_forward(qtbot):
    view = _view(qtbot)
    assert view.step() == 0 and not view.prev.isEnabled()
    for expected in range(1, G.STEP_COUNT):
        view.next.click()
        assert view.step() == expected
    assert not view.next.isEnabled()
    view.prev.click()
    assert view.step() == G.STEP_COUNT - 2


def test_selecting_a_provider_resets_to_first_step(qtbot):
    view = _view(qtbot)
    view.set_step(3)
    assert view.select("pexels") is True
    assert view.provider_id() == "pexels" and view.step() == 0
    assert view.select("uydurma") is False


def test_host_is_the_provider_site_and_copy_works(qtbot):
    view = _view(qtbot)
    assert view.provider_id() == "anthropic"
    view.copy.click()
    assert QApplication.clipboard().text() == "platform.claude.com"


def test_canvas_paints_every_step(qtbot):
    view = _view(qtbot)
    for step in range(G.STEP_COUNT):
        view.set_step(step)
        pixmap = view.canvas.grab()
        assert not pixmap.isNull()


def test_dialog_opens_on_requested_provider(qtbot):
    dialog = G.GuideDialog({"anthropic": "Anthropic", "pexels": "Pexels"}, start="pexels")
    qtbot.addWidget(dialog)
    assert dialog.view.provider_id() == "pexels"
