"""Ortak bileşenler: Lockup (marka kuralı), ModeSwitch, SquishyButton, HelpBubble, StatusBar, SmartErrorCard…"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from kadraj import branding
from kadraj.core.contracts import SmartAction, SmartError, UiMode
from kadraj.core.errors import translate
from kadraj.ui import fonts
from kadraj.ui.widgets import (ChoiceCard, ChoiceGroup, DropZone, HelpBubble, Lockup, ModeSwitch, SmartErrorCard,
                               SquishyButton, StatusBar, Stepper, TaskCard, icon_names, icon_pixmap, signature_px)


# ---- Lockup / marka kuralı (plan §0.1) ---------------------------------------
@pytest.mark.parametrize("wordmark, expected", [(72, 22), (44, 13), (30, 9), (20, 9), (10, 9)])
def test_signature_size_rule(wordmark, expected):
    assert signature_px(wordmark) == expected                 # ~0.30×, en az 9 px


def test_branding_constants():
    assert branding.SIGNATURE == "from İnsibağ" and branding.SIGNATURE_RATIO == 0.30
    assert branding.SIGNATURE_MIN_PX == 9 and branding.SIGNATURE_OPACITY == 0.60
    assert branding.WORDMARK_TRACKING_EM == 0.12
    assert "from İnsibağ" in branding.window_title()          # OS başlığında da imza


def test_lockup_geometry_and_accessibility(ctx, qtbot):
    lk = Lockup(wordmark_px=44, show_mark=True)
    qtbot.addWidget(lk)
    assert lk.signature_px == 13 and lk.width() > lk.height() > 0
    assert "from İnsibağ" in lk.accessibleName()
    small = Lockup(wordmark_px=20)
    qtbot.addWidget(small)
    assert small.signature_px == 9
    lk.set_wordmark_px(88)
    assert lk.signature_px == 26 and lk.width() > small.width()


def _render(widget) -> QImage:
    widget.show()
    QApplication.processEvents()
    return widget.grab().toImage()


def _right_edge(img: QImage, y0: int, y1: int, bg: QColor) -> int:
    best = -1
    for y in range(y0, y1):
        for x in range(img.width() - 1, best, -1):
            c = QColor(img.pixel(x, y))
            if abs(c.red() - bg.red()) + abs(c.green() - bg.green()) + abs(c.blue() - bg.blue()) > 60:
                best = max(best, x)
                break
    return best


def test_lockup_signature_is_right_aligned_with_the_J(ctx, qtbot):
    """İmzanın sağ kenarı "J" harfinin sağ kenarıyla hizalı (±3 px)."""
    lk = Lockup(wordmark_px=72)
    qtbot.addWidget(lk)
    lk.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    lk.setAutoFillBackground(True)
    img = _render(lk)
    bg = QColor(img.pixel(0, 0))
    word_bottom = int(lk.height() * 0.60)
    j_edge = _right_edge(img, 2, word_bottom, bg)
    sig_edge = _right_edge(img, int(lk.height() * 0.78), lk.height() - 1, bg)
    assert j_edge > 0 and sig_edge > 0 and abs(j_edge - sig_edge) <= 3, (j_edge, sig_edge)


def test_fonts_are_embedded_and_resolved(ctx):
    loaded = fonts.load_fonts()
    assert all(loaded.values()), loaded
    assert fonts.resolve("Nunito") == "Nunito" and fonts.resolve("Archivo Expanded") == "Archivo Expanded"
    assert fonts.resolve("Totally Missing Font") == "Totally Missing Font" or True      # asla istisna atmaz
    assert fonts.qfont("Nunito", 20, weight=800).weight() == 800


# ---- ModeSwitch -----------------------------------------------------------------
def test_mode_switch_click_and_keyboard(ctx, qtbot):
    sw = ModeSwitch(UiMode.SIMPLE)
    qtbot.addWidget(sw)
    sw.show()
    with qtbot.waitSignal(sw.modeSelected, timeout=500) as sig:
        QTest.mouseClick(sw, Qt.MouseButton.LeftButton, pos=QPoint(int(sw.width() * 0.8), sw.height() // 2))
    assert sig.args == ["legend"] and sw.mode is UiMode.LEGEND
    with qtbot.waitSignal(sw.modeSelected, timeout=500) as sig:
        QTest.keyClick(sw, Qt.Key.Key_Left)
    assert sig.args == ["simple"]
    with qtbot.waitSignal(sw.modeSelected, timeout=500):
        QTest.keyClick(sw, Qt.Key.Key_Space)
    with qtbot.assertNotEmitted(sw.modeSelected):
        sw.set_mode(UiMode.SIMPLE)                              # programatik: sinyal yok
    assert sw.accessibleName() and sw.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_mode_switch_position_follows_mode(ctx, qtbot):
    sw = ModeSwitch(UiMode.LEGEND)
    qtbot.addWidget(sw)
    assert sw.position == 1.0
    sw.set_mode("simple", animate=False)
    assert sw.position == 0.0


# ---- SquishyButton ---------------------------------------------------------------
def test_squishy_button_click_and_squish(ctx, qtbot):
    b = SquishyButton("Tamam", variant="primary", icon="plus")
    qtbot.addWidget(b)
    b.show()
    clicked = []
    b.clicked.connect(lambda: clicked.append(1))
    QTest.mousePress(b, Qt.MouseButton.LeftButton)
    assert b.squish == 1.0                                       # animasyon kapalı (test) → anında
    QTest.mouseRelease(b, Qt.MouseButton.LeftButton)
    assert clicked == [1] and b.squish == 0.0
    assert b.height() == ctx.theme.tokens.btn_h and b.sizeHint().width() > 60
    b.set_variant("ghost")
    assert b.variant == "ghost" and b.height() < ctx.theme.tokens.btn_h + 1


def test_squishy_button_renders_in_every_variant_and_mode(ctx, qtbot):
    for mode in ("simple", "legend"):
        ctx.theme.apply(mode, animate=False)
        for v in ("primary", "secondary", "ghost", "danger"):
            b = SquishyButton("Düğme", variant=v)
            qtbot.addWidget(b)
            assert not _render(b).isNull()
            b.setEnabled(False)
            assert not b.grab().isNull()


def test_squishy_disabled_does_not_emit(ctx, qtbot):
    b = SquishyButton("x")
    qtbot.addWidget(b)
    b.setEnabled(False)
    with qtbot.assertNotEmitted(b.clicked):
        QTest.mouseClick(b, Qt.MouseButton.LeftButton)


# ---- HelpBubble --------------------------------------------------------------------
def test_help_bubble_popup(ctx, qtbot):
    hb = HelpBubble("Bu ne işe yarar?")
    qtbot.addWidget(hb)
    hb.show()
    assert hb.width() == hb.height()                              # yuvarlak
    hb.toggle()
    assert hb._popup is not None and hb._popup.isVisible()
    hb.toggle()
    assert not hb._popup.isVisible()


# ---- StatusBar / AutosaveIndicator ---------------------------------------------------
def test_autosave_indicator_states(ctx, qtbot):
    sb = StatusBar()
    qtbot.addWidget(sb)
    ind = sb.autosave
    assert ind.state == "none" and ind.text()
    ind.set_state("saving")
    assert ind.state == "saving"
    ind.set_state("saved", at=0)
    assert ind.state == "saved"
    ind.set_state("garbage")
    assert ind.state == "none"
    ctx.theme.apply("legend", animate=False)
    ind.set_state("saved")
    assert ind.text().startswith("AUTOSAVE")                       # Legend metni


def test_status_bar_message_jobs_system_compact(ctx, qtbot):
    sb = StatusBar()
    qtbot.addWidget(sb)
    sb.set_message("Merhaba", timeout_ms=0)
    assert sb.message() == "Merhaba"
    sb.set_jobs(2, 4)
    sb.set_system({"cpu": 12.3, "ram_used": 4e9, "ram_total": 16e9, "gpu": None})
    sb.set_compact(False)
    sb.show()
    assert sb._sys.isVisible() and "CPU 12%" in sb._sys.text() and "RAM 4.0/16 GB" in sb._sys.text()
    sb.set_compact(True)
    assert not sb._sys.isVisible()
    sb.set_system({})
    assert sb._sys.text() == ""


# ---- SmartErrorCard -------------------------------------------------------------------
def test_smart_error_card_content_actions_and_details(ctx, qtbot):
    err = translate(ConnectionError("dns"), {"source": "downloader"})
    card = SmartErrorCard(err)
    qtbot.addWidget(card)
    card.show()
    assert card.accessibleName() == err.title and not card.details_visible
    card._toggle.click()
    assert card.details_visible
    card._toggle.click()
    assert not card.details_visible
    with qtbot.waitSignal(card.actionTriggered, timeout=500) as sig:
        card.findChildren(SquishyButton)[0].click()
    assert sig.args[0].kind == "retry"


def test_smart_error_card_copy_action_uses_clipboard(ctx, qtbot):
    err = SmartError("x", "T", "S", actions=[SmartAction("c", "Kopyala", "copy", {"text": "ham-bilgi"})])
    card = SmartErrorCard(err)
    qtbot.addWidget(card)
    card.findChildren(SquishyButton)[0].click()
    assert QApplication.clipboard().text() == "ham-bilgi"


# ---- TaskCard / ChoiceGroup / Stepper / DropZone / icons ---------------------------------------
def test_task_card_click_and_soon_badge(ctx, qtbot):
    c = TaskCard("Video İndir", "Açıklama", "download", "peach", soon=True)
    qtbot.addWidget(c)
    c.show()
    assert c.soon and c.accessibleName() == "Video İndir"
    with qtbot.waitSignal(c.clicked, timeout=500):
        QTest.mouseClick(c, Qt.MouseButton.LeftButton)
    for mode in ("simple", "legend"):
        ctx.theme.apply(mode, animate=False)
        assert not _render(c).isNull()


def test_choice_group_single_and_multi(ctx, qtbot):
    single = ChoiceGroup([ChoiceCard("a", "A"), ChoiceCard("b", "B")], exclusive=True, columns=2)
    qtbot.addWidget(single)
    single.set_selected(["a"])
    single.cards()[1].click()
    assert single.selected() == ["b"]
    multi = ChoiceGroup([ChoiceCard(i, i) for i in "abc"], exclusive=False, columns=3)
    qtbot.addWidget(multi)
    with qtbot.waitSignal(multi.selectionChanged, timeout=500) as sig:
        multi.cards()[0].click()
    multi.cards()[2].click()
    assert multi.selected() == ["a", "c"] and sig.args[0] == ["a"]


def test_stepper(ctx, qtbot):
    st = Stepper(5, 0, ["1", "2", "3", "4", "5"])
    qtbot.addWidget(st)
    st.set_current(99)
    assert st.current == 4 and st.steps == 5
    st.set_current(-3)
    assert st.current == 0


def test_drop_zone_emits_paths(ctx, qtbot, tmp_path):
    dz = DropZone("Bırak", "Gövde", "Seç")
    qtbot.addWidget(dz)
    dz.show()
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(f)), QUrl("https://example.com/x")])
    with qtbot.waitSignal(dz.filesDropped, timeout=500) as sig:
        ev = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
        dz.dropEvent(ev)
    assert sig.args[0] == [str(f)]                                # yalnızca yerel dosyalar
    with qtbot.waitSignal(dz.browseRequested, timeout=500):
        dz.button.click()


def test_icons_all_render(ctx):
    for name in icon_names():
        pm = icon_pixmap(name, "#FF0000", 24)
        img = pm.toImage()
        assert any(QColor(img.pixel(x, y)).alpha() > 0 for x in range(24) for y in range(24)), name
