"""Modüle özel widget'lar: eşlemeler, durumlar, iki modda ve erişilebilirlik katmanlarında boyama."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest

from kadraj.modules.recorder import widgets as W
from kadraj.modules.recorder.dsp import FLOOR_DB
from kadraj.modules.recorder.teleprompter import Prompter


@pytest.fixture(params=["simple", "legend"])
def themed(request, ctx):
    ctx.theme.apply(request.param, animate=False)
    return request.param


def painted(widget, qtbot):
    qtbot.addWidget(widget)
    widget.resize(widget.sizeHint().expandedTo(widget.minimumSizeHint()) + widget.size().__class__(40, 40))
    widget.show()
    return widget.grab().toImage()


def test_formatters():
    assert W.format_timecode(0) == "00:00:00:00" and W.format_timecode(75.5, 30) == "00:01:15:15"
    assert W.format_timecode(3661.0) == "01:01:01:00" and W.format_timecode(-3) == "00:00:00:00"
    assert W.format_clock(0) == "00:00" and W.format_clock(157.9) == "02:37" and W.format_clock(3725) == "1:02:05"
    assert W.db_to_fraction(-60) == 0 and W.db_to_fraction(0) == 1 and W.db_to_fraction(-30) == 0.5
    assert W.db_to_fraction(-200) == 0 and W.db_to_fraction(10) == 1


def test_segmented_vu_maps_db_to_20_segments(ctx):
    vu = W.SegmentedVU(2)
    vu.set_levels([FLOOR_DB, FLOOR_DB])
    assert vu.lit_segments == [0, 0]
    vu.set_levels([0.0, -30.0])
    assert vu.lit_segments == [20, 10]
    vu.set_levels([-12.0])                                                                 # mono kaynak iki satırı doldurur
    assert vu.lit_segments == [16, 16]
    vu.set_channels(1)
    assert len(vu.lit_segments) == 1 and vu.height() < W.SegmentedVU(2).height()


def test_segmented_vu_uses_the_design_colours(ctx):
    vu = W.SegmentedVU(1)
    ctx.theme.apply("simple", animate=False)
    cols = [vu._seg_color(i).name().upper() for i in (0, 11, 12, 15, 16, 17, 18, 19)]
    assert cols == ["#9FE0C1", "#9FE0C1", "#FFD97A", "#FFD97A", "#FFB38A", "#FFB38A", "#FF8FA3", "#FF8FA3"]


def test_stereo_meter_levels_hold_and_clip(ctx):
    m = W.StereoMeter(2)
    m.set_levels([-6.0, -60.0], [-3.0, -60.0], clipping=True)
    assert m.lit(0) > m.lit(1) == 0 and m._clip
    assert m.lit(0) == round(W.db_to_fraction(-6.0) * 32)
    m.set_levels([FLOOR_DB, FLOOR_DB])
    assert m.lit(0) == 0 and not m._clip


def test_gr_bar_and_lamp(ctx):
    g = W.GRBar()
    g.set_gr(-3)
    assert g.gr_db == 0.0
    g.set_gr(12.5)
    assert g.gr_db == 12.5
    lamp = W.StatusLamp("BYPASS")
    lamp.set_state("open", "OPEN")
    assert lamp.state == "open" and lamp.text == "OPEN"


def test_toggle_switch_click_and_keyboard(ctx, qtbot):
    t = W.ToggleSwitch()
    qtbot.addWidget(t)
    t.show()
    with qtbot.waitSignal(t.toggled, timeout=500) as sig:
        QTest.mouseClick(t, Qt.MouseButton.LeftButton)
    assert sig.args == [True] and t.isChecked()
    QTest.keyClick(t, Qt.Key.Key_Space)
    assert not t.isChecked()
    assert t.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_pill_button_exclusive_state(ctx, qtbot):
    p = W.PillButton("Yavaş")
    qtbot.addWidget(p)
    p.show()
    p.click()
    assert p.isChecked() and p.width() > 60


def test_record_button_states_and_accessible_names(ctx, qtbot):
    b = W.BigRecordButton()
    qtbot.addWidget(b)
    b.set_labels("Kayda başla", "Kaydı durdur")
    b.show()
    assert b.accessibleName() == "Kayda başla" and not b.recording and not b._timer.isActive()
    b.set_recording(True)
    assert b.accessibleName() == "Kaydı durdur" and b.recording and b._timer.isActive()
    b._pulse()
    b.set_recording(False)
    assert not b._timer.isActive() and b._phase == 0.0
    ctx.theme.apply("legend", animate=False)
    assert b.height() < 60 and b.width() > 80                                              # Legend: kompakt düğme
    ctx.theme.apply("simple", animate=False)
    assert b.height() >= 100                                                               # Basit: büyük daire


def test_waveform_view_states(ctx, qtbot):
    w = W.WaveformView()
    qtbot.addWidget(w)
    w.show()
    assert w.peaks is None and not w.grab().isNull()
    w.set_peaks([0.1, 0.9, 0.5])
    assert w.peaks == [0.1, 0.9, 0.5]
    w.set_peaks([])
    assert not w.grab().isNull()


def test_prompter_view_layout_and_current_line(ctx, qtbot):
    pr = Prompter("bir iki üç dört beş altı yedi sekiz dokuz on onbir oniki", wpm=60, font_px=36)
    v = W.PrompterView(pr)
    qtbot.addWidget(v)
    v.resize(300, 300)
    v.show()
    lines = v.layout_lines(250)
    assert lines and lines[0][0] == 0 and lines[-1][1] == len(pr.words) - 1
    assert all(a <= b for a, b, _ in lines) and [t for _a, _b, t in lines] == sorted([t for _a, _b, t in lines], key=lambda x: lines and [l[2] for l in lines].index(x))
    assert v.current_line(250) == 0
    pr.seek(len(pr.words) - 1)
    assert v.current_line(250) == len(lines) - 1
    pr.set_font(70)
    assert len(v.layout_lines(250)) > len(lines)                                           # büyük yazı → daha çok satır


def test_prompter_view_respects_paragraph_breaks_and_mirror(ctx, qtbot):
    pr = Prompter("kısa satır\n\nikinci paragraf", mirror=True)
    v = W.PrompterView(pr)
    qtbot.addWidget(v)
    v.resize(600, 300)
    v.show()
    assert [t for _a, _b, t in v.layout_lines(500)] == ["kısa satır", "ikinci paragraf"]
    img = v.grab().toImage()
    assert not img.isNull()


def test_prompter_view_wheel_requests_seek(ctx, qtbot):
    pr = Prompter("kelime " * 50)
    pr.seek(20)
    v = W.PrompterView(pr)
    qtbot.addWidget(v)
    v.show()
    with qtbot.waitSignal(v.seekRequested, timeout=500) as sig:
        ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        v.wheelEvent(ev)
    assert sig.args[0] == pytest.approx(20 - 4)


def test_empty_prompter_shows_the_hint_without_crashing(ctx, qtbot):
    v = W.PrompterView(Prompter(""))
    v.empty_text = "Metin boş"
    qtbot.addWidget(v)
    v.resize(400, 300)
    v.show()
    assert not v.grab().isNull() and v.current_line() == 0


@pytest.mark.parametrize("a11y", [dict(), dict(large_text=True), dict(high_contrast=True),
                                  dict(large_text=True, high_contrast=True)])
def test_all_widgets_paint_in_both_modes_and_a11y_layers(ctx, qtbot, themed, a11y):
    ctx.theme.apply(themed, animate=False, **a11y)
    pr = Prompter("birinci satır\nikinci satır\nüçüncü satır", font_px=30)
    pr.seek(4)
    widgets = [W.SegmentedVU(2), W.StereoMeter(2), W.GRBar(), W.StatusLamp("OPEN"), W.ToggleSwitch(),
               W.PillButton("Normal"), W.BigRecordButton(), W.WaveformView(), W.PrompterView(pr)]
    widgets[0].set_levels([-10, -20])
    widgets[1].set_levels([-6, -30], [-3, -20], True)
    widgets[2].set_gr(8)
    widgets[7].set_peaks([0.2, 0.8, 0.4, 0.6])
    for w in widgets:
        qtbot.addWidget(w)
        w.resize(max(w.width(), 220), max(w.height(), 160))
        w.show()
        img = w.grab().toImage()
        assert not img.isNull() and img.width() > 0, type(w).__name__
