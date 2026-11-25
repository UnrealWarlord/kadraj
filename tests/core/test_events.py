from __future__ import annotations

import threading

from kadraj.core.events import EventBus


def test_subscribe_emit_unsubscribe(qtbot):
    bus = EventBus()
    got = []
    off = bus.subscribe("t", got.append)
    bus.emit("t", 1)
    bus.emit("other", 2)
    assert got == [1]
    off()
    bus.emit("t", 3)
    assert got == [1]


def test_failing_subscriber_does_not_break_others(qtbot, capsys):
    bus = EventBus()
    got = []
    bus.subscribe("t", lambda _p: 1 / 0)
    bus.subscribe("t", got.append)
    bus.emit("t", "ok")
    assert got == ["ok"]


def test_emit_from_other_thread_is_delivered_on_main_thread(qtbot):
    bus = EventBus()
    seen = []
    bus.subscribe("t", lambda p: seen.append((p, threading.current_thread() is threading.main_thread())))
    t = threading.Thread(target=lambda: bus.emit("t", "x"))
    t.start()
    t.join()
    qtbot.waitUntil(lambda: bool(seen), timeout=1000)
    assert seen == [("x", True)]
