"""Otomatik kayıt + 10 Dk Önceye Döndür halkası."""
from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from kadraj.core.contracts import AutosaveAPI, SmartError, Topics
from kadraj.storage import autosave as A
from kadraj.storage.autosave import AutosaveService


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, sec):
        self.t += sec


@pytest.fixture()
def env(ctx, tmp_path):
    clock = Clock()
    ctx.autosave.stop()
    svc = AutosaveService(ctx, clock=clock)            # gerçek saat yerine sahte saat; eski servis durduruldu
    ctx.autosave = svc
    ctx.create_project("Otokayıt", tmp_path / "p")
    events = []
    ctx.bus.subscribe(Topics.AUTOSAVE_STATE, events.append)
    ctx.events = events
    return ctx, svc, clock


def edit(ctx, name):
    ctx.project.data.name = name
    ctx.project.mark_dirty()


def project_name_on_disk(ctx):
    return json.loads(ctx.project.file_path.read_text(encoding="utf-8"))["name"]


def test_implements_contract_and_runs_with_project(env):
    ctx, svc, _ = env
    assert isinstance(svc, AutosaveAPI) and svc.running and svc.interval_seconds == 20
    ctx.project.close()
    assert not svc.running and svc.state == "none"


def test_baseline_ring_entry_at_open_and_no_write_without_changes(env):
    ctx, svc, clock = env
    assert len(svc.ring()) == 1 and svc.state == "idle"
    mtime = ctx.project.file_path.stat().st_mtime_ns
    files = sorted(p.name for p in (ctx.project.root / ".kadraj" / "autosave").iterdir())
    for _ in range(5):
        clock.advance(20)
        svc.tick()
    assert ctx.project.file_path.stat().st_mtime_ns == mtime                      # diske hiç dokunulmadı
    assert sorted(p.name for p in (ctx.project.root / ".kadraj" / "autosave").iterdir()) == files


def test_tick_saves_only_when_changed_and_silently(env, qtbot):
    ctx, svc, clock = env
    saved_signals = []
    ctx.project.saved.connect(saved_signals.append)
    edit(ctx, "Değişti")
    assert ctx.project.dirty and project_name_on_disk(ctx) == "Otokayıt"
    clock.advance(20)
    svc.tick()
    assert project_name_on_disk(ctx) == "Değişti" and not ctx.project.dirty and svc.state == "saved"
    assert saved_signals == []                                                      # "kaydedildi" iletisi yok
    states = [e["state"] for e in ctx.events]
    qtbot.waitUntil(lambda: "saved" in [e["state"] for e in ctx.events], timeout=500)
    assert states[:1] == ["saving"] or "saving" in [e["state"] for e in ctx.events]
    last = [e for e in ctx.events if e["state"] == "saved"][-1]
    assert last["at"] == clock.t and last["ring"] >= 1


def test_dirty_flag_without_content_change_still_clears(env):
    ctx, svc, clock = env
    ctx.project.mark_dirty()
    clock.advance(20)
    svc.tick()
    assert not ctx.project.dirty


def test_ring_one_entry_per_minute_window_and_newest_content_wins(env):
    ctx, svc, clock = env
    base = len(svc.ring())
    for i in range(3):                                   # aynı dakika içinde 3 kayıt → tek giriş
        edit(ctx, f"v{i}")
        clock.advance(15)
        svc.tick()
    assert len(svc.ring()) == base + 1
    newest = json.loads(Path(svc.ring()[-1].path).read_text(encoding="utf-8"))
    assert newest["project"]["name"] == "v2"
    clock.advance(61)
    edit(ctx, "v3")
    svc.tick()
    assert len(svc.ring()) == base + 2


def test_ring_is_pruned_by_count_not_by_time(env):
    ctx, svc, clock = env
    for i in range(20):
        edit(ctx, f"n{i}")
        clock.advance(61)
        svc.tick()
    ring = svc.ring()
    assert len(ring) == A.RING_ENTRIES == 11
    assert [e.time for e in ring] == sorted(e.time for e in ring)
    clock.advance(3 * 3600)                              # saatlerce boşta kalmak geçmişi silmez
    svc.tick()
    assert len(svc.ring()) == 11


def drive(ctx, svc, clock, names, step=120):
    for n in names:
        clock.advance(step)
        edit(ctx, n)
        svc.tick()


def test_rewind_restores_the_state_from_ten_minutes_ago(env):
    ctx, svc, clock = env
    drive(ctx, svc, clock, ["A", "B", "C", "D", "E", "F", "G"])          # 0:"Otokayıt" 2:A 4:B 6:C 8:D 10:E 12:F 14:G
    res = svc.rewind(10)                                                 # hedef = 14-10 = 4. dk → en yeni kayıt ≤ hedef = B
    assert res.ok and res.restored and res.reason == "restored" and res.minutes_requested == 10
    assert abs(res.minutes_actual - 10.0) < 0.01
    assert ctx.project.data.name == "B" and project_name_on_disk(ctx) == "B" and not ctx.project.dirty


def test_rewind_partial_when_history_is_shorter(env):
    ctx, svc, clock = env
    drive(ctx, svc, clock, ["A"])                                        # yalnızca 2 dk geçmiş
    res = svc.rewind(10)
    assert res.restored and ctx.project.data.name == "Otokayıt" and abs(res.minutes_actual - 2.0) < 0.01


def test_rewind_no_change_no_history_no_project(env):
    ctx, svc, clock = env
    res = svc.rewind(10)
    assert res.ok and not res.restored and res.reason == "no_change"
    for e in svc.ring():
        Path(e.path).unlink()
    assert svc.rewind(10).reason == "no_history"
    ctx.project.close()
    assert svc.rewind(10).reason == "no_project" and not svc.rewind(10).ok


def test_rewind_is_undoable_and_captures_unsaved_edits_first(env):
    ctx, svc, clock = env
    drive(ctx, svc, clock, ["A", "B", "C", "D", "E", "F", "G"])
    edit(ctx, "H-kaydedilmemiş")                                         # tick beklemeden döndür
    assert svc.rewind(10).restored and ctx.project.data.name == "B"
    undone = svc.undo_rewind()
    assert undone.restored and ctx.project.data.name == "H-kaydedilmemiş" and project_name_on_disk(ctx) == "H-kaydedilmemiş"
    assert svc.undo_rewind().reason == "no_history"                      # yedek tüketildi


def test_only_three_backups_are_kept(env):
    ctx, svc, clock = env
    for i in range(6):
        clock.advance(120)
        edit(ctx, f"x{i}")
        svc.tick()
        clock.advance(1)
        svc.rewind(1)
        clock.advance(1)
    assert len(svc._backups()) <= A.MAX_BACKUPS


def test_rewind_resets_undo_stack_and_announces(env, qtbot):
    from PySide6.QtGui import QUndoCommand
    ctx, svc, clock = env
    ctx.undo.push(QUndoCommand("eski komut"))
    got = []
    ctx.bus.subscribe(Topics.PROJECT_RESTORED, got.append)
    replaced = []
    ctx.project.dataReplaced.connect(replaced.append)
    drive(ctx, svc, clock, ["A", "B", "C", "D", "E", "F", "G"])
    svc.rewind(10)
    assert not ctx.undo.canUndo() and replaced == ["rewind"]
    qtbot.waitUntil(lambda: bool(got), timeout=500)
    assert got[0]["reason"] == "rewind" and got[0]["label"]


def test_bus_rewind_request_restores_and_notifies(env, qtbot):
    ctx, svc, clock = env
    notes = []
    ctx.bus.subscribe(Topics.NOTIFY, notes.append)
    drive(ctx, svc, clock, ["A", "B", "C", "D", "E", "F", "G"])
    ctx.bus.emit(Topics.REWIND_REQUESTED, {"minutes": 10})
    qtbot.waitUntil(lambda: bool(notes), timeout=1000)
    assert ctx.project.data.name == "B" and "10" in notes[0]["text"] and notes[0]["level"] == "info"


def test_bus_rewind_with_nothing_to_do_and_bad_payload(env, qtbot):
    ctx, svc, clock = env
    notes = []
    ctx.bus.subscribe(Topics.NOTIFY, notes.append)
    ctx.bus.emit(Topics.REWIND_REQUESTED, None)                         # yük yok → varsayılan 10 dk
    ctx.bus.emit(Topics.REWIND_REQUESTED, {"minutes": "x"})
    qtbot.waitUntil(lambda: len(notes) == 2, timeout=1000)
    assert all("10" in n["text"] for n in notes)                         # "10 dk öncekiyle aynı"


def test_bus_partial_message_mentions_actual_minutes(env, qtbot):
    ctx, svc, clock = env
    notes = []
    ctx.bus.subscribe(Topics.NOTIFY, notes.append)
    drive(ctx, svc, clock, ["A"])
    ctx.bus.emit(Topics.REWIND_REQUESTED, {"minutes": 10})
    qtbot.waitUntil(lambda: bool(notes), timeout=1000)
    assert "2 dk" in notes[0]["text"]


def test_save_errors_do_not_crash_and_are_reported_once(env, monkeypatch):
    ctx, svc, clock = env
    errors = []
    ctx.bus.subscribe(Topics.ERROR, errors.append)
    from PySide6.QtWidgets import QApplication
    real = A._write_json

    def boom(path, obj):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(A, "_write_json", boom)
    edit(ctx, "disk dolu")
    for _ in range(3):
        clock.advance(20)
        svc.tick()                                                       # asla istisna atmaz
    QApplication.processEvents()
    assert svc.state == "error" and ctx.project.dirty
    assert len(errors) == 1 and isinstance(errors[0], SmartError) and errors[0].code == "disk.full"
    monkeypatch.setattr(A, "_write_json", real)
    clock.advance(20)
    svc.tick()
    assert svc.state == "saved" and not ctx.project.dirty and project_name_on_disk(ctx) == "disk dolu"
    monkeypatch.setattr(A, "_write_json", boom)                          # yeni hata yine bir kez bildirilir
    edit(ctx, "yine dolu")
    svc.tick()
    QApplication.processEvents()
    assert len(errors) == 2


def test_pending_changes_are_saved_when_the_project_closes(env):
    ctx, svc, clock = env
    edit(ctx, "kapanırken")
    path = ctx.project.file_path
    ctx.project.close()
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "kapanırken"


def test_interval_follows_settings_with_a_floor(env):
    ctx, svc, _ = env
    ctx.settings.set("autosave_seconds", 7)
    assert svc.interval_seconds == 7
    ctx.settings.set("autosave_seconds", 1)
    assert svc.interval_seconds == A.MIN_INTERVAL_S
    ctx.settings.set("autosave_seconds", "abc")
    assert svc.interval_seconds == 20


def test_tick_without_project_is_a_noop(ctx):
    ctx.autosave.tick()
    assert ctx.autosave.state == "none" and ctx.autosave.ring() == []


def test_stop_ignores_later_rewind_requests(env, qtbot):
    ctx, svc, clock = env
    drive(ctx, svc, clock, ["A", "B", "C", "D", "E", "F", "G"])
    svc.stop()
    ctx.bus.emit(Topics.REWIND_REQUESTED, {"minutes": 10})
    qtbot.wait(50)
    assert ctx.project.data.name == "G"


def test_reopening_a_project_starts_a_fresh_ring_baseline(env, tmp_path):
    ctx, svc, clock = env
    root = ctx.project.root
    edit(ctx, "oturum1")
    ctx.project.close()
    clock.advance(30)
    ctx.open_project(root)
    assert ctx.project.data.name == "oturum1" and svc.running
    assert json.loads(Path(svc.ring()[-1].path).read_text(encoding="utf-8"))["project"]["name"] == "oturum1"
