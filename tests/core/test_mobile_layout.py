"""Mobil 6a–6c (M1, M2): telefon yalnız Basit Mod ve ekrana sığar; tablet iki modlu; dokunmatik yardımcılar.

Telefon/tablet görünümü masaüstünde ``KADRAJ_FORM_FACTOR`` ile taklit edilir (ekransız Qt). Ölçüler mantıksal piksel:
telefon 393 × 851 (Pixel 8 gibi), tablet dikey 800 × 1280.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QScroller, QScrollArea, QVBoxLayout, QWidget

from kadraj import device
from kadraj.core import i18n
from kadraj.core.contracts import UiMode, WORKSPACES
from kadraj.ui import adaptive
from kadraj.ui.widgets.flow import FlowLayout

PHONE_W, PHONE_H = 393, 851
TABLET_W, TABLET_H = 800, 1280


@pytest.fixture()
def phone(monkeypatch):
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "phone")


@pytest.fixture()
def tablet(monkeypatch):
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "tablet")


def _pump(app, n: int = 4) -> None:
    for _ in range(n):
        app.processEvents()


def _window(ctx, qtbot, width: int, height: int):
    from kadraj.ui.shell.main_window import MainWindow
    win = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(win)
    win.setMinimumSize(0, 0)
    win.resize(width, height)
    win.show()
    qtbot.waitExposed(win)
    return win


# ------------------------------------------------------------------------------------------------ modlar
def test_phone_stays_in_simple_mode(ctx, phone):
    assert not device.legend_allowed()
    ctx.set_ui_mode(UiMode.LEGEND)
    assert ctx.ui_mode is UiMode.SIMPLE


def test_tablet_and_desktop_keep_both_modes(ctx, tablet):
    assert device.legend_allowed()
    ctx.set_ui_mode(UiMode.LEGEND)
    assert ctx.ui_mode is UiMode.LEGEND


def test_phone_top_bar_fits_and_has_no_mode_switch(ctx, phone, qtbot, qapp):
    from kadraj.ui.shell.topbar import PHONE_MIN_WIDTH, TopBar
    bar = TopBar()
    qtbot.addWidget(bar)
    bar.resize(PHONE_MIN_WIDTH, 60)
    bar.show()                                                          # telefonda çubuk hep sıkışık (yalnız ikon)
    qtbot.waitExposed(bar)
    assert bar.mode_switch.isHidden() and bar.rewind_btn.isHidden() and bar.autosave.isHidden()
    for name in ("", "Belgesel", "Çok uzun bir belgesel projesi", "W" * 30):
        bar.set_project_name(name)
        _pump(qapp)
        assert bar.minimumSizeHint().width() <= PHONE_MIN_WIDTH, name
    assert bar.project_btn.toolTip() == "W" * 30 and bar.project_btn.text().endswith("… ▾")
    short = bar.project_btn.text()
    bar.resize(PHONE_W + 200, 60)                                       # yan çevrilince ad daha uzun görünür
    _pump(qapp)
    assert len(bar.project_btn.text()) > len(short)


def test_tablet_top_bar_fits_portrait_in_both_modes(ctx, tablet, qtbot, qapp):
    from kadraj.ui.shell.topbar import TABLET_MIN_WIDTH
    win = _window(ctx, qtbot, TABLET_MIN_WIDTH, 960)                    # 7" tablet, dikey
    bar = win.topbar
    for mode in (UiMode.SIMPLE, UiMode.LEGEND):
        ctx.set_ui_mode(mode)
        _pump(qapp)
        assert bar.narrow and bar.rewind_btn.isHidden() and bar.autosave.isHidden()
        assert bar.rewind_action.isVisible() and bar.rewind_action in bar.project_menu.actions()
        for name in ("Yaz Tatili", "W" * 30):
            bar.set_project_name(name)
            _pump(qapp)
            assert bar.layout().minimumSize().width() <= TABLET_MIN_WIDTH, (mode, name)
    assert bar.project_btn.text().endswith("… ▾") and bar.project_btn.toolTip() == "W" * 30
    win.resize(TABLET_W, TABLET_H)                                      # 10" tablet, dikey: kısa ad tam görünür
    bar.set_project_name("Yaz Tatili")
    _pump(qapp)
    assert bar.project_btn.text() == "Yaz Tatili ▾"
    win.resize(TABLET_H, TABLET_W)                                      # yan çevrilince masaüstündeki gibi
    _pump(qapp)
    assert not bar.narrow and bar.rewind_btn.isVisibleTo(bar) and not bar.rewind_action.isVisible()
    assert bar.layout().minimumSize().width() <= TABLET_H


@pytest.mark.parametrize("size", [(TABLET_W, TABLET_H), (TABLET_H, TABLET_W)], ids=["dikey", "yatay"])
def test_tablet_legend_editor_keeps_the_timeline_on_screen(ctx, tablet, qtbot, qapp, size):
    from PySide6.QtWidgets import QScrollArea
    win = _window(ctx, qtbot, *size)
    ctx.set_ui_mode(UiMode.LEGEND)
    win.show_workspace("edit")
    _pump(qapp, 8)
    pan = next(a for a in win.legend.findChildren(QScrollArea) if a.property(adaptive.PAN))
    assert pan.widget().height() <= pan.viewport().height()             # masaüstündeki gibi ekrana sıkışır, dikeyde kaymaz
    timeline = next(w for w in win.legend.findChildren(QWidget) if type(w).__name__ == "TimelineView")
    top = timeline.mapTo(pan.viewport(), QPoint(0, 0)).y()
    assert 0 < top < pan.viewport().height()                            # zaman çizelgesi ekranda


# ------------------------------------------------------------------------------------------------ sayfalar telefona sığar
def _too_wide(root: QWidget, limit: int):
    """``root`` içinde en küçük genişliği ``limit``'i aşan en alttaki görünür öğeler. Sayfanın dikey kaydırma alanının
    içi denetlenir; yalnız kasıtlı yatay kaydırma alanlarının (``adaptive.PAN``: sahne panosu, tablet dikeyde Legend
    sayfası) ve listelerin içi hariç."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView, QAbstractScrollArea

    def inside_scroll(w: QWidget) -> bool:
        p = w.parentWidget()
        while p is not None and p is not root:
            if isinstance(p, QAbstractItemView):
                return True
            if p.property(adaptive.PAN):
                return True                                             # yatayda kayan alan: geniş içerik kasıtlı
            p = p.parentWidget()
        return False

    out = []
    for w in root.findChildren(QWidget):
        if not w.isVisibleTo(root) or isinstance(w, QAbstractScrollArea) or w.isWindow() or inside_scroll(w):
            continue
        if w.minimumSizeHint().width() <= limit:
            continue
        kids = [c for c in w.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
                if c.isVisibleTo(w) and not isinstance(c, QAbstractScrollArea)]
        if any(c.minimumSizeHint().width() > limit for c in kids):
            continue
        out.append(f"{type(w).__name__}#{w.objectName()}({w.minimumSizeHint().width()})")
    return out


def test_every_simple_page_fits_a_phone(ctx, phone, qtbot, qapp):
    from kadraj.ui.shell.topbar import PHONE_MIN_WIDTH
    win = _window(ctx, qtbot, PHONE_MIN_WIDTH, 800)
    _pump(qapp)
    limit = PHONE_MIN_WIDTH - 20                                        # sayfa çerçevesinin kenar boşluğu
    assert not _too_wide(win.simple.currentWidget(), limit), "ana ekran"
    for spec in ctx.registry.specs():
        win.open_module(spec.id)
        _pump(qapp)
        assert ctx.current_module == spec.id
        assert not _too_wide(win.simple.currentWidget(), limit), spec.id
    win.go_home()


def test_tablet_portrait_fits_legend_workspaces(ctx, tablet, qtbot, qapp):
    win = _window(ctx, qtbot, TABLET_W, TABLET_H)
    ctx.set_ui_mode(UiMode.LEGEND)
    _pump(qapp)
    for ws in WORKSPACES:
        win.show_workspace(ws)
        _pump(qapp)
        assert win.minimumSizeHint().width() <= TABLET_W, ws            # paneller dahil pencere dikey ekrana sığar
        assert not _too_wide(win.legend, TABLET_W - 40), ws


def test_home_hides_the_drop_zone_on_touch_devices(ctx, phone, qtbot, qapp):
    win = _window(ctx, qtbot, PHONE_W, PHONE_H)
    _pump(qapp)
    from kadraj.ui.widgets.drop_zone import DropZone
    zones = [z for z in win.findChildren(DropZone)]
    assert zones and all(z.isHidden() for z in zones)


def test_home_cards_fill_the_phone_width_after_a_wider_layout(ctx, phone, qtbot, qapp):
    win = _window(ctx, qtbot, 1320, 860)                                # önce geniş: üç sütun
    _pump(qapp)
    home = win.simple.currentWidget()
    assert home._columns == 3
    win.resize(PHONE_W, PHONE_H)
    _pump(qapp)
    assert home._columns == 1
    grid = home._grid_host.width()
    assert all(c.width() >= grid - 2 for c in home.cards), (grid, [c.width() for c in home.cards])


def test_card_description_wraps_clear_of_the_badge(ctx, qtbot):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QFont
    from kadraj.ui.widgets import TaskCard
    card = TaskCard("Ses Kaydet", "Mikrofonunla tertemiz, stüdyo gibi bir ses kaydı.", "mic", "lilac", chip=".wav")
    qtbot.addWidget(card)
    font = QFont()
    font.setPixelSize(14)
    box = QRectF(0, 0, 170, 70)                                          # dar kart: ikinci satır rozet hizasında
    assert card.desc_hits_badges(font, box, box.right() - 50, 20)
    assert not card.desc_hits_badges(font, QRectF(0, 0, 900, 70), 900 - 50, 20)   # tek satır, rozete ulaşmaz


def test_status_message_elides_but_keeps_the_full_text(qtbot):
    from kadraj.ui.widgets import StatusBar
    bar = StatusBar()
    qtbot.addWidget(bar)
    bar.resize(200, 30)
    bar.show()
    qtbot.waitExposed(bar)
    text = "Basit Mod açıldı — projen aynen duruyor, kaldığın yerden devam edebilirsin."
    bar.set_message(text, 0)
    assert bar.message() == text                                         # tam metin (ipucu, testler)
    assert bar._message.text().endswith("…") and bar._message.width() < 200
    assert bar.minimumSizeHint().width() < bar._message.fontMetrics().horizontalAdvance(text)   # şeridi genişletmez
    bar.resize(1400, 30)
    qtbot.waitUntil(lambda: bar._message.text() == text)


# ------------------------------------------------------------------------------------------------ yardımcılar
def test_hbox_wraps_only_on_touch_devices(monkeypatch, qapp):
    assert not isinstance(adaptive.hbox(), FlowLayout)
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "phone")
    assert isinstance(adaptive.hbox(), FlowLayout)
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "tablet")
    assert isinstance(adaptive.hbox(), FlowLayout)


def test_flow_layout_is_one_line_when_it_fits_and_wraps_when_not(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    flow = FlowLayout(host, spacing=8)
    buttons = []
    for _i in range(3):
        b = QWidget()                                                   # sabit boyutlu öğe (uygulama QSS'i dokunmaz)
        b.setFixedSize(100, 30)
        flow.addWidget(b)
        buttons.append(b)
    field = QLineEdit()
    flow.addWidget(field, 1)                                            # esneme payı: sarılınca tam satır
    flow.setGeometry(QRect(0, 0, 600, 200))
    centers = {b.geometry().center().y() for b in buttons} | {field.geometry().center().y()}
    assert max(centers) - min(centers) <= 1                              # tek satır, dikeyde ortalı
    assert field.geometry().left() > buttons[-1].geometry().right()     # tek satırda kalan genişliği alır
    narrow = 220
    flow.setGeometry(QRect(0, 0, narrow, 400))
    assert buttons[2].geometry().y() > buttons[0].geometry().y()        # sığmayan alt satıra geçti
    assert field.geometry().width() == narrow                            # tam satır öğe
    assert flow.heightForWidth(narrow) > flow.heightForWidth(600)
    assert flow.minimumSize().width() <= narrow


def test_squishy_button_shrinks_and_elides_only_on_phone(monkeypatch, qapp):
    from kadraj.ui.widgets.squishy_button import PHONE_MIN_WIDTH, SquishyButton
    text = "Bu düğmenin metni telefon ekranı için oldukça uzun kalıyor"
    desktop = SquishyButton(text)
    assert desktop.minimumSizeHint() == desktop.sizeHint()
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "phone")
    small = SquishyButton(text)
    assert small.minimumSizeHint().width() <= PHONE_MIN_WIDTH < small.sizeHint().width()
    assert small.text() == text                                          # tam metin kalır (erişilebilirlik, ipucu)


def test_breakable_adds_invisible_breaks_only_on_phone(monkeypatch):
    path = "/storage/emulated/0/Android/data/org.insibag.kadraj/files/Documents"
    assert adaptive.breakable(path) == path
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "phone")
    soft = adaptive.breakable(path)
    assert soft != path and adaptive.plain(soft) == path and adaptive.ZWSP in soft


def test_current_only_stack_sizes_to_the_visible_page(qapp):
    stack = adaptive.CurrentOnlyStack()
    wide, narrow = QWidget(), QWidget()
    wide.setMinimumWidth(1500)
    narrow.setMinimumWidth(300)
    stack.addWidget(narrow)
    stack.addWidget(wide)
    stack.setCurrentIndex(0)
    assert stack.minimumSizeHint().width() < 1500                       # gizli geniş sayfa ekranı genişletmez
    stack.setCurrentIndex(1)
    assert stack.minimumSizeHint().width() >= 1500


def _wrapping_page(words: int) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    label = QLabel(" ".join(["kelime"] * words))
    label.setWordWrap(True)                                             # genişliğe bağlı yükseklik
    lay.addWidget(label)
    return page


@pytest.mark.parametrize("make", [adaptive.CurrentOnlyStack, adaptive.CurrentOnlyTabs])
def test_current_only_containers_take_height_for_width_from_the_visible_page(qapp, make):
    box = make()
    short, tall = _wrapping_page(5), _wrapping_page(400)
    for page in (short, tall):
        box.addWidget(page) if isinstance(box, adaptive.CurrentOnlyStack) else box.addTab(page, "x")
    box.setCurrentIndex(0)
    assert box.hasHeightForWidth()
    assert box.heightForWidth(300) < tall.heightForWidth(300) / 2       # gizli uzun sayfa boş yer açtırmaz
    box.setCurrentIndex(1)
    assert box.heightForWidth(300) >= tall.heightForWidth(300)


def test_dialogs_fit_the_screen_on_touch_devices(monkeypatch, qtbot):
    from PySide6.QtWidgets import QDialog
    from kadraj.ui.scaling import work_area
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "phone")
    dlg = QDialog()
    qtbot.addWidget(dlg)
    adaptive.fit_dialog(dlg, 5000, 5000)
    area = work_area(dlg)
    assert dlg.width() <= area.width() and dlg.height() <= area.height()


# ------------------------------------------------------------------------------------------------ dokunma
def _scroll_page(rows: int = 40):
    area = QScrollArea()
    area.setWidgetResizable(True)
    body = QWidget()
    lay = QVBoxLayout(body)
    for i in range(rows):
        lay.addWidget(QLabel(f"satır {i}"))
    area.setWidget(body)
    area.resize(360, 400)
    return area, body, lay


def test_touch_scroll_binds_each_area_once_and_respects_opt_out(monkeypatch, qtbot):
    area, _body, _lay = _scroll_page()
    qtbot.addWidget(area)
    assert adaptive.touch_scroll_all(area) == 0                         # masaüstü: dokunulmaz
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "phone")
    assert adaptive.touch_scroll_all(area) == 1 and QScroller.hasScroller(area.viewport())
    assert adaptive.touch_scroll_all(area) == 0                         # ikinci kez bağlanmaz
    other, _b, _l = _scroll_page()
    qtbot.addWidget(other)
    other.setProperty(adaptive.NO_FLICK, True)
    assert adaptive.touch_scroll_all(other) == 0


class _Drag(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(60)
        self.log = []

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.log.append("p")

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        self.log.append("m")

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self.log.append("r")


def test_direct_touch_drags_the_widget_instead_of_scrolling(monkeypatch, qtbot):
    monkeypatch.setenv("KADRAJ_FORM_FACTOR", "phone")
    area, _body, lay = _scroll_page()
    drag = _Drag()
    lay.insertWidget(0, drag)
    qtbot.addWidget(area)
    area.show()
    qtbot.waitExposed(area)
    adaptive.touch_scroll_all(area)
    assert adaptive.direct_touch(drag)
    dev = QTest.createTouchDevice()
    win = area.window()
    p = drag.mapTo(win, QPoint(30, 30))
    QTest.touchEvent(win, dev).press(0, p, win).commit()
    for _ in range(8):
        p = p + QPoint(0, -15)
        QTest.touchEvent(win, dev).move(0, p, win).commit()
        QTest.qWait(10)
    QTest.touchEvent(win, dev).release(0, p, win).commit()
    QTest.qWait(50)
    assert drag.log[0] == "p" and drag.log[-1] == "r" and "m" in drag.log
    assert area.verticalScrollBar().value() == 0                        # sayfa kaymadı


def test_kanban_moves_cards_without_dragging_on_touch_devices(ctx, phone, qtbot):
    from kadraj.modules.series.strings import STRINGS
    from kadraj.modules.series.widgets import KanbanBoard
    i18n.register_strings(STRINGS)
    board = KanbanBoard()
    qtbot.addWidget(board)
    assert board.move_box is not None
    moved = []
    board.cardMoved.connect(lambda sid, st: moved.append((sid, st)))
    board.selected_id = "s1"
    board.move_box.setCurrentIndex(board.move_box.findData("approved"))
    board.move_box.activated.emit(board.move_box.currentIndex())
    assert moved == [("s1", "approved")] and board.move_box.currentIndex() == 0


def test_desktop_kanban_has_no_move_box(ctx, qtbot):
    from kadraj.modules.series.strings import STRINGS
    from kadraj.modules.series.widgets import KanbanBoard
    i18n.register_strings(STRINGS)
    board = KanbanBoard()
    qtbot.addWidget(board)
    assert board.move_box is None


def test_recording_folder_path_wraps_on_a_phone_even_with_backslashes(ctx, phone, qtbot):
    """Windows yolunun "\\" ayırıcısında Qt satır kırmaz: kayıt yeri etiketi telefon genişliğini aşıyordu (Windows CI)."""
    from kadraj.modules.recorder.location import RecordingLocationBar
    from kadraj.ui.shell.topbar import PHONE_MIN_WIDTH
    bar = RecordingLocationBar()
    qtbot.addWidget(bar)
    bar.set_folder(r"C:\Users\runneradmin\AppData\Local\Temp\pytest-of-runneradmin\pytest-0"
                   r"\test_every_simple_page_fits_a0\KADRAJ_Projeler\Hizli_Kayitlar\02_Audio_WAV")
    assert bar.label.minimumSizeHint().width() <= PHONE_MIN_WIDTH - 20
    assert adaptive.plain(bar.label.text()).endswith(r"Hizli_Kayitlar\02_Audio_WAV")
