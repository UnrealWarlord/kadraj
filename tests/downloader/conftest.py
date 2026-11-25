"""Medya İndirici testleri: sahte yt-dlp, oda dolu disk varsayımı, servis fikstürü."""
from __future__ import annotations

import pytest

from kadraj.modules.downloader import service as service_mod

from .fakes import FakeYDL, default_scenario


@pytest.fixture(autouse=True)
def _reset_fake_ydl():
    FakeYDL.scenario = default_scenario()
    FakeYDL.opts_seen = []
    yield
    FakeYDL.scenario = default_scenario()


@pytest.fixture(autouse=True)
def _roomy_disk(monkeypatch):
    # Gerçek disk durumu testleri etkilemesin (CI'da az boş alan olabilir).
    monkeypatch.setattr(service_mod.disk, "warning_for", lambda _path: None)


@pytest.fixture()
def svc(ctx):
    service = ctx.service("downloader")
    assert service is not None
    service.ydl_factory = FakeYDL          # ağ yok: her çözümleme ve indirme sahte
    return service
