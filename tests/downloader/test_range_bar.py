"""Aralık çubuğu: sınırlar, en az aralık, klavye, sinyal."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from kadraj.modules.downloader.range_bar import MIN_GAP, RangeBar


def _bar(qtbot, duration=225.0):
    bar = RangeBar()
    bar.resize(400, 44)
    qtbot.addWidget(bar)
    bar.set_duration(duration)
    return bar


def test_defaults_span_whole_clip(qtbot):
    bar = _bar(qtbot)
    assert bar.values() == (0.0, 225.0) and bar.duration() == 225.0


def test_values_are_clamped_and_keep_minimum_gap(qtbot):
    bar = _bar(qtbot)
    bar.set_values(-5, 9999)
    assert bar.values() == (0.0, 225.0)
    bar.set_values(100, 100.2)
    s, e = bar.values()
    assert e - s >= MIN_GAP


def test_keyboard_moves_active_handle_and_emits(qtbot):
    bar = _bar(qtbot)
    bar.set_values(80, 200)
    bar.setFocus()
    with qtbot.waitSignal(bar.valuesChanged, timeout=1000) as blocker:
        QTest.keyClick(bar, Qt.Key.Key_Right)          # end +1 (varsayılan etkin tutamaç: end)
    assert blocker.args == [80.0, 201.0]
    QTest.keyClick(bar, Qt.Key.Key_Up)                 # başlangıca geç
    QTest.keyClick(bar, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)   # start -10
    assert bar.values() == (70.0, 201.0)


def test_handles_cannot_cross(qtbot):
    bar = _bar(qtbot)
    bar.set_values(10, 20)
    bar._set_handle("start", 50)                       # başlangıç bitişi geçemez
    s, e = bar.values()
    assert s <= e - MIN_GAP
    bar._set_handle("end", -3)                         # bitiş başlangıcın altına inemez
    s, e = bar.values()
    assert e >= s + MIN_GAP
