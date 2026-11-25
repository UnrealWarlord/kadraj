"""Proje dosyası ve oturumu: oluşturma, açma, kaydetme, bozuk/yabancı/yeni dosya, ileri uyumluluk."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kadraj.core.contracts import (HIDDEN_DIR, HIDDEN_SUBDIRS, PROJECT_FILE_NAME, PROJECT_FORMAT,
                                   PROJECT_SUBFOLDERS, SmartError, UiMode)
from kadraj.storage import project as P
from kadraj.storage.project import ProjectData, ProjectSession


def test_create_makes_folders_file_and_db(tmp_path, qtbot):
    s = ProjectSession()
    root = s.create(tmp_path, "Yaz Tatili: Vlog/2")
    assert root.parent == tmp_path and ":" not in root.name and "/" not in root.name
    for f in PROJECT_SUBFOLDERS:
        assert (root / f).is_dir()
    for sub in HIDDEN_SUBDIRS:
        assert (root / HIDDEN_DIR / sub).is_dir()
    data = json.loads((root / PROJECT_FILE_NAME).read_text(encoding="utf-8"))
    assert data["format"] == PROJECT_FORMAT and data["version"] == 1 and data["name"] == "Yaz Tatili: Vlog/2"
    assert data["settings"] == {"fps": 30, "resolution": "1920x1080", "sample_rate": 48000}
    assert data["timeline"] == {"tracks": []} and data["markers"] == []
    assert (root / ".kadraj" / "proje.db").is_file() and s.is_open and not s.dirty
    s.close()


def test_unique_dir_never_overwrites(tmp_path):
    s = ProjectSession()
    a = s.create(tmp_path, "Aynı Ad")
    b = s.create(tmp_path, "Aynı Ad")
    assert a != b and a.is_dir() and b.is_dir()
    s.close()


def test_save_and_reopen_roundtrip(tmp_path, qtbot):
    s = ProjectSession()
    root = s.create(tmp_path, "P", ui_mode="legend", settings={"fps": 24})
    s.data.markers.append({"t": 12.5, "label": "bölüm 1", "chapter": True})
    s.data.timeline["tracks"].append({"id": "v1", "kind": "video", "clips": []})
    s.mark_dirty()
    assert s.dirty
    with qtbot.waitSignal(s.saved, timeout=500):
        assert s.save()
    assert not s.dirty
    s.close()
    t = ProjectSession()
    t.open(root)
    assert t.name == "P" and t.data.ui_mode_last == "legend" and t.data.settings["fps"] == 24
    assert t.data.markers[0]["label"] == "bölüm 1" and t.data.timeline["tracks"][0]["id"] == "v1"
    t.close()


def test_open_accepts_the_project_file_itself(tmp_path):
    s = ProjectSession()
    root = s.create(tmp_path, "P")
    s.close()
    t = ProjectSession()
    assert t.open(root / PROJECT_FILE_NAME) == root
    t.close()


def test_unknown_fields_survive_a_save(tmp_path):
    s = ProjectSession()
    root = s.create(tmp_path, "P")
    s.close()
    f = root / PROJECT_FILE_NAME
    raw = json.loads(f.read_text(encoding="utf-8"))
    raw["from_the_future"] = {"x": [1, 2]}
    f.write_text(json.dumps(raw), encoding="utf-8")
    t = ProjectSession()
    t.open(root)
    t.mark_dirty()
    t.save()
    assert json.loads(f.read_text(encoding="utf-8"))["from_the_future"] == {"x": [1, 2]}
    t.close()


@pytest.mark.parametrize("content, code", [
    ("{ bozuk", "project.unreadable"),
    ('{"format": "soilclasspro-project", "version": 2}', "project.foreign"),
    ("[1, 2]", "project.foreign"),
    (json.dumps({"format": PROJECT_FORMAT, "version": 99}), "project.too_new"),
])
def test_bad_project_files_raise_smart_errors(tmp_path, content, code):
    (tmp_path / PROJECT_FILE_NAME).write_text(content, encoding="utf-8")
    with pytest.raises(SmartError) as ei:
        P.read_project_file(tmp_path / PROJECT_FILE_NAME)
    assert ei.value.code == code and ei.value.summary and ei.value.technical


def test_missing_project_file(tmp_path):
    with pytest.raises(SmartError) as ei:
        ProjectSession().open(tmp_path)
    assert ei.value.code == "project.missing"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    d = ProjectData(name="x")
    P.write_project_file(tmp_path / PROJECT_FILE_NAME, d)
    P.write_project_file(tmp_path / PROJECT_FILE_NAME, d)
    assert [p.name for p in tmp_path.iterdir()] == [PROJECT_FILE_NAME]


def test_ui_mode_last_is_saved_silently_on_close(tmp_path):
    s = ProjectSession()
    root = s.create(tmp_path, "P")
    s.set_ui_mode_last(UiMode.LEGEND)
    assert not s.dirty                                    # "kaydet?" diye rahatsız etmez
    s.close()
    assert json.loads((root / PROJECT_FILE_NAME).read_text(encoding="utf-8"))["ui_mode_last"] == "legend"


def test_subfolder_helper(tmp_path):
    s = ProjectSession()
    root = s.create(tmp_path, "P")
    assert s.subfolder("02_Audio_WAV") == root / "02_Audio_WAV"
    with pytest.raises(ValueError):
        s.subfolder("../etc")
    s.close()
    with pytest.raises(RuntimeError):
        s.subfolder("01_Downloads")


def test_closed_session_state(qtbot):
    s = ProjectSession()
    assert not s.is_open and s.root is None and s.name == "" and s.save() is False
    s.mark_dirty()
    assert not s.dirty


def test_signals(tmp_path, qtbot):
    s = ProjectSession()
    with qtbot.waitSignal(s.opened, timeout=500):
        s.create(tmp_path, "P")
    with qtbot.waitSignal(s.dirtyChanged, timeout=500) as sig:
        s.mark_dirty()
    assert sig.args == [True]
    with qtbot.waitSignal(s.closed, timeout=500):
        s.close()


def test_safe_folder_name():
    assert P.safe_folder_name('a/b\\c:d*e?"f<g>h|i') == "a b c d e f g h i"
    assert P.safe_folder_name("   ...  ") == "Yeni Proje"
    assert len(P.safe_folder_name("x" * 500)) == 80


def test_project_data_tolerates_garbage_fields():
    d = ProjectData.from_dict({"format": PROJECT_FORMAT, "name": None, "settings": "x", "timeline": 5,
                               "markers": "no", "series": [], "ui_mode_last": "weird", "created": "yesterday"})
    assert d.name == "Adsız Proje" and d.ui_mode_last == "simple" and d.timeline == {"tracks": []}
    assert d.markers == [] and d.series == {} and d.settings["fps"] == 30
