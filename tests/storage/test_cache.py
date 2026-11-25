"""Önbellek temizleyici: kategoriler, koruma listesi, kuru çalışma, yaş filtresi, simge bağlantı."""
from __future__ import annotations

import os
import time

import pytest

from kadraj.core.contracts import HIDDEN_DIR
from kadraj.storage import cache


def _write(path, size, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture()
def project(tmp_path):
    p = tmp_path / "Proje"
    _write(p / HIDDEN_DIR / "cache" / "thumb.bin", 100)
    _write(p / "06_Proxies" / "klip_proxy.mp4", 200)
    _write(p / "01_Downloads" / "film.mp4.part", 50)
    _write(p / "01_Downloads" / "film2.mp4.part-Frag3", 30)
    _write(p / "01_Downloads" / "sarki.webm.ytdl", 10)
    _write(p / "01_Downloads" / "benim.particles.mp4", 40)     # yarım indirme değil
    _write(p / "02_Audio_WAV" / "kayit.wav", 999)              # dokunulmaz
    _write(p / HIDDEN_DIR / "autosave" / "auto.json", 70)      # dokunulmaz
    _write(p / HIDDEN_DIR / "proje.db", 80)                    # dokunulmaz
    _write(p / "proje.kadraj", 20)                             # dokunulmaz
    return p


def test_usage_counts_each_category(project):
    u = cache.usage(project)
    assert (u["cache"].files, u["cache"].bytes) == (1, 100)
    assert (u["proxies"].files, u["proxies"].bytes) == (1, 200)
    assert (u["partial"].files, u["partial"].bytes) == (3, 90)


def test_clean_removes_only_cache_categories(project):
    report = cache.clean(project)
    assert (report.removed, report.freed, report.failed) == (5, 390, 0)
    assert (project / "02_Audio_WAV" / "kayit.wav").exists()
    assert (project / HIDDEN_DIR / "autosave" / "auto.json").exists()
    assert (project / HIDDEN_DIR / "proje.db").exists()
    assert (project / "proje.kadraj").exists()
    assert (project / "01_Downloads" / "benim.particles.mp4").exists()
    assert cache.usage(project)["partial"].files == 0


def test_dry_run_deletes_nothing(project):
    report = cache.clean(project, dry_run=True)
    assert report.dry_run and report.removed == 5
    assert cache.usage(project)["proxies"].files == 1


def test_single_category_only(project):
    cache.clean(project, [cache.CATEGORY_PROXIES])
    u = cache.usage(project)
    assert u["proxies"].files == 0
    assert u["cache"].files == 1
    assert u["partial"].files == 3


def test_older_than_days_keeps_fresh_files(project):
    now = time.time()
    old = now - 40 * 86400
    os.utime(project / "06_Proxies" / "klip_proxy.mp4", (old, old))
    report = cache.clean(project, older_than_days=30, now=now)
    assert report.removed == 1
    assert not (project / "06_Proxies" / "klip_proxy.mp4").exists()
    assert (project / HIDDEN_DIR / "cache" / "thumb.bin").exists()


def test_symlinked_folder_outside_project_is_untouched(tmp_path):
    project = tmp_path / "Proje"
    project.mkdir()
    outside = tmp_path / "disari"
    _write(outside / "kritik.mp4", 10)
    (project / "06_Proxies").symlink_to(outside, target_is_directory=True)
    report = cache.clean(project, [cache.CATEGORY_PROXIES])
    assert report.removed == 0
    assert (outside / "kritik.mp4").exists()


def test_missing_project_is_empty(tmp_path):
    assert cache.usage(tmp_path / "yok")["cache"].files == 0
    assert cache.clean(tmp_path / "yok").removed == 0


def test_failed_removal_is_counted_not_raised(project, monkeypatch):
    real_remove = os.remove

    def flaky(path):
        if str(path).endswith("klip_proxy.mp4"):
            raise PermissionError("kilitli")
        real_remove(path)

    monkeypatch.setattr(cache.os, "remove", flaky)
    report = cache.clean(project)
    assert report.failed == 1
    assert report.removed == 4
