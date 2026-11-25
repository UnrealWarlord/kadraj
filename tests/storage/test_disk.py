"""Disk alanı: eşikler, ölçüm noktası, iş öncesi kontrol, Türkçe kartlar. Gerçek disk ölçümü yok."""
from __future__ import annotations

from collections import namedtuple

import pytest

from kadraj.core.contracts import Severity
from kadraj.storage import disk

Usage = namedtuple("Usage", "total used free")
GIB = disk.GIB


def _fake(free_bytes):
    return lambda _path: Usage(total=500 * GIB, used=500 * GIB - free_bytes, free=free_bytes)


@pytest.mark.parametrize("free_gib, level", [
    (50, disk.Level.OK),
    (10, disk.Level.OK),
    (9.9, disk.Level.WARNING),
    (2, disk.Level.WARNING),
    (1.9, disk.Level.CRITICAL),
    (0, disk.Level.CRITICAL),
])
def test_thresholds(monkeypatch, tmp_path, free_gib, level):
    monkeypatch.setattr(disk.shutil, "disk_usage", _fake(int(free_gib * GIB)))
    assert disk.status(tmp_path).level is level


def test_nonexistent_path_measures_nearest_existing_parent(monkeypatch, tmp_path):
    seen = []

    def spy(path):
        seen.append(path)
        return Usage(total=GIB, used=0, free=GIB)

    monkeypatch.setattr(disk.shutil, "disk_usage", spy)
    disk.status(tmp_path / "yeni" / "alt")
    assert seen and disk.Path(seen[0]).resolve() == tmp_path.resolve()


def test_measurement_failure_is_unknown_and_never_blocks(monkeypatch, tmp_path):
    def boom(_path):
        raise OSError("ölçülemedi")

    monkeypatch.setattr(disk.shutil, "disk_usage", boom)
    assert disk.status(tmp_path).level is disk.Level.UNKNOWN
    assert disk.warning_for(tmp_path) is None
    assert disk.has_room_for(tmp_path, 10 ** 15) is True


def test_has_room_for_respects_reserve(monkeypatch, tmp_path):
    monkeypatch.setattr(disk.shutil, "disk_usage", _fake(5 * GIB))
    assert disk.has_room_for(tmp_path, 2 * GIB)          # 5 - 2 = 3 ≥ 2 GB rezerv
    assert not disk.has_room_for(tmp_path, 4 * GIB)      # 5 - 4 = 1 < 2 GB rezerv
    assert disk.has_room_for(tmp_path, 1 * GIB, reserve=0)


def test_low_space_card_is_warning_with_turkish_text(monkeypatch, tmp_path):
    monkeypatch.setattr(disk.shutil, "disk_usage", _fake(5 * GIB))
    card = disk.warning_for(tmp_path)
    assert card is not None
    assert card.code == "disk.low" and card.severity is Severity.WARNING
    assert "5,0 GB" in card.summary and card.title and card.steps
    assert "KADRAJ" not in (card.title + card.summary + card.cause).upper()


def test_critical_card_is_error(monkeypatch, tmp_path):
    monkeypatch.setattr(disk.shutil, "disk_usage", _fake(GIB))
    card = disk.warning_for(tmp_path)
    assert card.code == "disk.critical" and card.severity is Severity.ERROR
    assert "1,0 GB" in card.summary


def test_plenty_of_space_has_no_card(monkeypatch, tmp_path):
    monkeypatch.setattr(disk.shutil, "disk_usage", _fake(50 * GIB))
    assert disk.warning_for(tmp_path) is None


def test_insufficient_card_mentions_needed_and_free(monkeypatch, tmp_path):
    monkeypatch.setattr(disk.shutil, "disk_usage", _fake(5 * GIB))
    card = disk.insufficient_error(tmp_path, 4 * GIB)
    assert card.code == "disk.insufficient"
    assert "4,0 GB" in card.summary and "5,0 GB" in card.summary


@pytest.mark.parametrize("n, text", [
    (0, "0 B"), (-5, "0 B"), (1536, "1,5 KB"), (3 * GIB, "3,0 GB"),
])
def test_human_size(n, text):
    assert disk.human_size(n) == text
