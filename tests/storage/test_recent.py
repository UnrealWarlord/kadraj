"""Son projeler: sıra, tekrar, sınır, eksik yollar, bozuk dosya, atomik yazma."""
from __future__ import annotations

import json

import pytest

from kadraj import paths
from kadraj.core.contracts import MAX_RECENT
from kadraj.storage import recent


@pytest.fixture()
def base(tmp_path):
    folder = tmp_path / "projeler"
    folder.mkdir()
    return folder


def _mk(base, name):
    p = base / name
    p.mkdir()
    return p


def test_empty_when_no_file(clean_home):
    assert recent.get_recent() == []


def test_newest_first(clean_home, base):
    recent.add_recent(_mk(base, "a"))
    recent.add_recent(_mk(base, "b"))
    assert [e.name for e in recent.get_recent()] == ["b", "a"]


def test_readd_moves_to_top_without_duplicates(clean_home, base):
    a, b = _mk(base, "a"), _mk(base, "b")
    recent.add_recent(a)
    recent.add_recent(b)
    recent.add_recent(a)
    entries = recent.get_recent()
    assert [e.name for e in entries] == ["a", "b"]
    assert len({e.path for e in entries}) == 2


def test_name_override_and_default_basename(clean_home, base):
    recent.add_recent(_mk(base, "klip"), "Benim Klibim")
    assert recent.get_recent()[0].name == "Benim Klibim"
    recent.add_recent(_mk(base, "diger"))
    assert recent.get_recent()[0].name == "diger"


def test_capped_at_max_recent(clean_home, base):
    for i in range(MAX_RECENT + 3):
        recent.add_recent(_mk(base, f"p{i}"))
    entries = recent.get_recent()
    assert len(entries) == MAX_RECENT
    assert entries[0].name == f"p{MAX_RECENT + 2}"


def test_missing_directory_is_hidden_but_kept_in_file(clean_home, base):
    a, b = _mk(base, "a"), _mk(base, "b")
    recent.add_recent(a)
    recent.add_recent(b)
    b.rmdir()
    assert [e.name for e in recent.get_recent()] == ["a"]
    raw = json.loads(paths.recent_file().read_text(encoding="utf-8"))
    assert len(raw["recent"]) == 2


def test_adding_missing_path_is_ignored(clean_home, tmp_path):
    recent.add_recent(tmp_path / "yok")
    assert not paths.recent_file().exists()
    assert recent.get_recent() == []


def test_forget_and_clear_leave_projects_alone(clean_home, base):
    a, b = _mk(base, "a"), _mk(base, "b")
    recent.add_recent(a)
    recent.add_recent(b)
    recent.forget(a)
    assert [e.name for e in recent.get_recent()] == ["b"]
    recent.clear()
    assert recent.get_recent() == []
    assert a.is_dir() and b.is_dir()


def test_corrupt_file_reads_as_empty_and_recovers(clean_home, base):
    paths.recent_file().parent.mkdir(parents=True, exist_ok=True)
    paths.recent_file().write_text("{bozuk", encoding="utf-8")
    assert recent.get_recent() == []
    recent.add_recent(_mk(base, "a"))
    assert [e.name for e in recent.get_recent()] == ["a"]


@pytest.mark.parametrize("content", ["[1, 2]", '{"recent": "yazi"}', '{"recent": [1, "x", null]}'])
def test_wrong_shape_reads_as_empty(clean_home, content):
    paths.recent_file().parent.mkdir(parents=True, exist_ok=True)
    paths.recent_file().write_text(content, encoding="utf-8")
    assert recent.get_recent() == []


def test_write_leaves_no_temp_files(clean_home, base):
    recent.add_recent(_mk(base, "a"))
    leftovers = [p for p in paths.recent_file().parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_unwritable_store_is_silent(clean_home, base, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk yok")

    monkeypatch.setattr(recent.tempfile, "mkstemp", boom)
    recent.add_recent(_mk(base, "a"))
    assert recent.get_recent() == []
