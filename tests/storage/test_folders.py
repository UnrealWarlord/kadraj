"""Proje klasör iskeleti: düzen, idempotentlik, silmeme, kök oluşturma."""
from __future__ import annotations

import pytest

from kadraj.core.contracts import HIDDEN_DIR, HIDDEN_SUBDIRS, PROJECT_SUBFOLDERS
from kadraj.storage import folders


def test_folder_map_matches_contract_layout():
    assert tuple(folders.FOLDER_BY_KIND.values()) == PROJECT_SUBFOLDERS


@pytest.mark.parametrize("kind, expected", [
    ("download", "01_Downloads"), ("wav", "02_Audio_WAV"), ("ai_image", "03_AI_Images"),
    ("ai_scene", "04_AI_Scenes"), ("script", "05_Scripts"), ("proxy", "06_Proxies"),
    ("export", "07_Exports"),
])
def test_folder_for_maps_each_kind(kind, expected):
    assert folders.folder_for(kind) == expected


def test_folder_for_unknown_kind_raises():
    with pytest.raises(ValueError):
        folders.folder_for("video")


def test_ensure_project_folders_creates_full_layout(tmp_path):
    project = tmp_path / "Projem"
    created = folders.ensure_project_folders(project)
    assert [p.name for p in created] == list(PROJECT_SUBFOLDERS)
    assert all(p.is_dir() for p in created)
    for sub in HIDDEN_SUBDIRS:
        assert (project / HIDDEN_DIR / sub).is_dir()


def test_ensure_is_idempotent_and_never_deletes(tmp_path):
    project = tmp_path / "P"
    folders.ensure_project_folders(project)
    clip = project / "01_Downloads" / "klip.mp4"
    clip.write_bytes(b"veri")
    marker = project / "proje.kadraj"
    marker.write_text("{}", encoding="utf-8")

    again = folders.ensure_project_folders(project)

    assert len(again) == len(PROJECT_SUBFOLDERS)
    assert clip.read_bytes() == b"veri"
    assert marker.exists()


def test_ensure_root_creates_nested_and_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "ev"))             # Linux/macOS: expanduser HOME'a bakar
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "ev"))      # Windows: expanduser / Path.home() USERPROFILE'a bakar
    root = folders.ensure_root("~/Videos/Projeler")
    assert root == tmp_path / "ev" / "Videos" / "Projeler"
    assert root.is_dir()


def test_blocked_path_raises_oserror(tmp_path):
    blocker = tmp_path / "dosya"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        folders.ensure_project_folders(blocker)
