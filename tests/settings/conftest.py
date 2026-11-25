"""Ayarlar testleri: sahte kasa, sahte ağ/pip/alt süreç, servis fikstürü. Gerçek ağ yok."""
from __future__ import annotations

import pytest

from kadraj.core.contracts import PingResult

from .fakes import FULL_KEY, FakeHttp, FakePopen, FakeRun, FakeVault


@pytest.fixture()
def vault(ctx):
    fake = FakeVault({"anthropic": FULL_KEY})
    ctx.vault = fake                    # AppContext.vault düz özniteliktir
    return fake


@pytest.fixture()
def svc(ctx, vault):
    service = ctx.service("settings")
    assert service is not None
    service.http_get = FakeHttp()
    service.run_cmd = FakeRun()
    service.popen = FakePopen()
    service.ping_fn = lambda pid: PingResult(True, 42.0, "ok")
    return service
