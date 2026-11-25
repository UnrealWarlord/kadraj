"""Snapshot ağacı: ebeveynli dallar, geri yükleme (geri alınabilir), karşılaştırma, budama, kalıcılık."""
from __future__ import annotations

import json

import pytest

from kadraj.core.contracts import SmartError, SnapshotAPI, Topics
from kadraj.storage import snapshots as S


@pytest.fixture()
def env(ctx, tmp_path):
    ctx.create_project("Snap", tmp_path / "p")
    return ctx, ctx.snapshots


def track(tid, *clips):
    return {"id": tid, "kind": "video", "clips": [{"id": c, "in": 0, "out": 5} for c in clips]}


def mutate(ctx, name=None, tracks=None, markers=None, settings=None):
    d = ctx.project.data
    if name is not None:
        d.name = name
    if tracks is not None:
        d.timeline["tracks"] = tracks
    if markers is not None:
        d.markers = markers
    if settings:
        d.settings.update(settings)
    ctx.project.mark_dirty()


def err(fn):
    with pytest.raises(SmartError) as ei:
        fn()
    return ei.value.code


def test_contract_and_requires_open_project(ctx):
    assert isinstance(ctx.snapshots, SnapshotAPI)
    for call in (lambda: ctx.snapshots.create("x"), lambda: ctx.snapshots.list(), lambda: ctx.snapshots.restore(1)):
        assert err(call) == "snapshot.no_project"


def test_create_writes_file_row_and_pointer(env):
    ctx, snaps = env
    a = snaps.create("ilk")
    assert a.id == 1 and a.branch == "main" and a.label == "ilk" and a.parent_id is None and not a.auto
    f = ctx.project.root / ".kadraj" / "snapshots" / f"{a.id}.json"
    assert f.is_file() and json.loads(f.read_text(encoding="utf-8"))["project"]["name"] == "Snap"
    assert ctx.project.db.get_snapshot(a.id)["path"] == f".kadraj/snapshots/{a.id}.json"
    assert not list((ctx.project.root / ".kadraj" / "snapshots").glob("pending-*"))
    assert snaps.current() == ("main", a.id) and ctx.project.dirty                     # işaretçi projeyle kaydedilir
    b = snaps.create("ikinci")
    assert b.parent_id == a.id and snaps.current() == ("main", b.id)
    assert [s.label for s in snaps.list()] == ["ilk", "ikinci"] and snaps.branches() == ["main"]


def test_restore_goes_back_and_is_reversible(env, qtbot):
    ctx, snaps = env
    mutate(ctx, "v1", [track("t1", "c1")])
    s1 = snaps.create("v1")
    mutate(ctx, "v2", [track("t1", "c1", "c2")])
    announced = []
    ctx.bus.subscribe(Topics.PROJECT_RESTORED, announced.append)
    info = snaps.restore(s1.id)
    assert info.id == s1.id and ctx.project.data.name == "v1" and len(ctx.project.data.timeline["tracks"][0]["clips"]) == 1
    assert snaps.current() == ("main", s1.id)
    assert json.loads(ctx.project.file_path.read_text(encoding="utf-8"))["name"] == "v1"        # kalıcı
    pre = [s for s in snaps.list() if s.auto]
    assert len(pre) == 1 and pre[0].label == "Geri yüklemeden önce"                              # yedek alındı
    snaps.restore(pre[0].id)                                                                     # geri alınabilir
    assert ctx.project.data.name == "v2" and len(ctx.project.data.timeline["tracks"][0]["clips"]) == 2
    qtbot.waitUntil(lambda: len(announced) == 2, timeout=500)
    assert announced[0]["reason"] == "snapshot"


def test_restore_without_changes_does_not_create_a_backup(env):
    ctx, snaps = env
    s1 = snaps.create("a")
    snaps.restore(s1.id)
    assert [s.auto for s in snaps.list()] == [False]


def test_restore_clears_the_undo_stack(env):
    from PySide6.QtGui import QUndoCommand
    ctx, snaps = env
    s1 = snaps.create("a")
    mutate(ctx, "b")
    ctx.undo.push(QUndoCommand("x"))
    snaps.restore(s1.id)
    assert not ctx.undo.canUndo()


def test_branching_creates_a_named_branch_with_parent(env):
    ctx, snaps = env
    mutate(ctx, "ana-1")
    s1 = snaps.create("ana-1")
    mutate(ctx, "ana-2")
    s2 = snaps.create("ana-2")
    info = snaps.branch_from(s1.id, "alternatif sahne")
    assert info.id == s1.id and snaps.current() == ("alternatif sahne", s1.id) and ctx.project.data.name == "ana-1"
    mutate(ctx, "alt-1")
    b1 = snaps.create("alt-1")
    assert b1.branch == "alternatif sahne" and b1.parent_id == s1.id
    b2 = snaps.create("alt-2")
    assert b2.parent_id == b1.id
    assert snaps.branches() == ["main", "alternatif sahne"]
    assert [s.label for s in snaps.list("alternatif sahne")] == ["alt-1", "alt-2"]
    assert [s.label for s in snaps.list("main") if not s.auto] == ["ana-1", "ana-2"]
    back = snaps.create("ana-3", branch="main")                       # ana dala dön: ucunun çocuğu
    assert back.branch == "main" and back.parent_id == s2.id
    assert err(lambda: snaps.branch_from(s1.id, "main")) == "snapshot.branch_exists"
    for bad in ("", "  ", "a/b", "x" * 49, "a|b"):
        assert err(lambda: snaps.branch_from(s1.id, bad)) == "snapshot.invalid_branch"


def test_tree_shape(env):
    ctx, snaps = env
    s1 = snaps.create("1")
    s2 = snaps.create("2")
    mutate(ctx, "x")
    snaps.branch_from(s1.id, "dal")
    s3 = snaps.create("3")
    tree = snaps.tree()
    assert len(tree) == 1 and tree[0]["info"].id == s1.id
    kids = sorted(c["info"].id for c in tree[0]["children"])
    assert kids[0] == s2.id and s3.id in kids


def test_compare_summarises_differences(env):
    ctx, snaps = env
    mutate(ctx, "Önce", [track("v1", "c1", "c2"), track("a1", "m1")], [{"t": 1, "label": "giriş"}], {"fps": 24})
    a = snaps.create("a")
    mutate(ctx, "Sonra", [track("v1", "c1", "c3", "c4"), track("fx1")], [{"t": 1, "label": "giriş"},
                                                                         {"t": 9, "label": "final"}], {"fps": 30})
    b = snaps.create("b")
    d = snaps.compare(a.id, b.id)
    assert not d.identical and (d.a, d.b) == (a.id, b.id)
    text = "\n".join(d.summary)
    assert "“Önce” → “Sonra”" in text and "fps: 24 → 30" in text
    assert d.changes["tracks_added"] == ["fx1"] and d.changes["tracks_removed"] == ["a1"]
    assert d.changes["clips"]["v1"] == {"added": 2, "removed": 1, "changed": 0}
    assert d.changes["markers"] == {"added": 1, "removed": 0} and "1 kanal eklendi" in text and "1 kanal silindi" in text
    assert snaps.compare(a.id, a.id).identical


def test_compare_detects_changed_clips_series_and_other_fields(env):
    ctx, snaps = env
    mutate(ctx, tracks=[track("v1", "c1")])
    a = snaps.create("a")
    ctx.project.data.timeline["tracks"][0]["clips"][0]["out"] = 99
    ctx.project.data.series = {"stage": "bible"}
    ctx.project.data.extra["bilinmeyen"] = 1
    ctx.project.data.timeline["zoom"] = 2
    b = snaps.create("b")
    d = snaps.compare(a.id, b.id)
    assert d.changes["clips"]["v1"]["changed"] == 1 and d.changes["series"] is True
    assert d.changes["other"] == ["bilinmeyen"] and d.changes["timeline_other"] is True


def test_compare_with_current(env):
    ctx, snaps = env
    a = snaps.create("a")
    assert snaps.compare_with_current(a.id).identical                  # işaretçi farkı sayılmaz
    mutate(ctx, "değişti")
    d = snaps.compare_with_current(a.id)
    assert not d.identical and d.b == -1 and "değişti" in d.summary[0]


def test_delete_reparents_children_and_moves_head(env):
    ctx, snaps = env
    s1, s2, s3 = snaps.create("1"), snaps.create("2"), snaps.create("3")
    f2 = ctx.project.root / ".kadraj" / "snapshots" / f"{s2.id}.json"
    assert snaps.delete(s2.id) is True and not f2.exists()
    assert next(s for s in snaps.list() if s.id == s3.id).parent_id == s1.id
    assert snaps.delete(s3.id) is True and snaps.current() == ("main", s1.id)         # head ebeveyne geçti
    assert snaps.delete(999) is False
    assert err(lambda: snaps.restore(s2.id)) == "snapshot.not_found"


def test_prune_auto_keeps_manual_snapshots(env):
    ctx, snaps = env
    manual = snaps.create("elle")
    for i in range(6):
        snaps.create(f"oto{i}", auto=True)
    assert snaps.prune_auto(keep=2) == 4
    labels = [s.label for s in snaps.list()]
    assert "elle" in labels and labels.count("oto4") == 1 and labels.count("oto5") == 1 and len(labels) == 3


def test_rename(env):
    _, snaps = env
    a = snaps.create("eski")
    assert snaps.rename(a.id, "yeni").label == "yeni" and snaps.list()[0].label == "yeni"


def test_missing_snapshot_file_is_a_smart_error(env):
    ctx, snaps = env
    a = snaps.create("a")
    (ctx.project.root / ".kadraj" / "snapshots" / f"{a.id}.json").unlink()
    assert err(lambda: snaps.restore(a.id)) == "snapshot.file_missing"
    (ctx.project.root / ".kadraj" / "snapshots" / f"{a.id}.json").write_text("{bozuk")
    assert err(lambda: snaps.compare(a.id, a.id)) == "snapshot.file_missing"


def test_tree_and_pointer_survive_close_and_reopen(env):
    ctx, snaps = env
    root = ctx.project.root
    mutate(ctx, "kalıcı")
    s1 = snaps.create("1")
    snaps.branch_from(s1.id, "dalım")
    s2 = snaps.create("2")
    ctx.project.save()
    ctx.project.close()
    ctx.open_project(root)
    assert [s.label for s in ctx.snapshots.list()] == ["1", "2"]
    assert ctx.snapshots.current() == ("dalım", s2.id) and ctx.snapshots.branches() == ["main", "dalım"]
    ctx.snapshots.restore(s1.id)
    assert ctx.project.data.name == "kalıcı"


def test_diff_projects_is_pure_and_tolerates_garbage():
    lines, changes = S.diff_projects({}, {})
    assert lines == [] and changes == {}
    lines, _ = S.diff_projects({"timeline": {"tracks": "nope"}, "markers": [1, 2]}, {"timeline": None, "markers": "x"})
    assert isinstance(lines, list)
