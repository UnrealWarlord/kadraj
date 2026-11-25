"""Sistem izleyici: psutil, pynvml (yoksa sessiz), API ping."""
from __future__ import annotations

import types

from kadraj.core.contracts import SysmonAPI
from kadraj.core.sysmon import SystemMonitor, _KEYS

KEYS = set(_KEYS)


class FakePsutil:
    @staticmethod
    def cpu_percent(interval=None):
        return 37.5

    @staticmethod
    def virtual_memory():
        return types.SimpleNamespace(used=6_000_000_000, total=16_000_000_000)

    @staticmethod
    def disk_usage(path):
        return types.SimpleNamespace(free=400_000_000_000, total=1_000_000_000_000)


class FakeNvml:
    def __init__(self, fail_after=None):
        self.calls, self.fail_after = 0, fail_after

    def nvmlInit(self): pass
    def nvmlDeviceGetHandleByIndex(self, i): return "h0"
    def nvmlDeviceGetName(self, h): return b"RTX Test 4070"

    def nvmlDeviceGetUtilizationRates(self, h):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("driver gone")
        return types.SimpleNamespace(gpu=61)

    def nvmlDeviceGetMemoryInfo(self, h):
        return types.SimpleNamespace(used=7_800_000_000, total=12_000_000_000)


def test_real_sample_has_all_keys_and_sane_values():
    s = SystemMonitor().sample()
    assert set(s) == KEYS
    assert s["cpu"] is None or 0 <= s["cpu"] <= 100
    assert s["ram_total"] and s["ram_used"] <= s["ram_total"] and s["disk_free"] and s["disk_total"]
    assert isinstance(SystemMonitor(), SysmonAPI)


def test_fake_psutil_and_nvml_values():
    s = SystemMonitor(nvml=FakeNvml(), psutil_module=FakePsutil).sample("/")
    assert s["cpu"] == 37.5 and s["ram_used"] == 6_000_000_000 and s["disk_free"] == 400_000_000_000
    assert s["gpu"] == 61.0 and s["vram_used"] == 7_800_000_000 and s["vram_total"] == 12_000_000_000
    assert s["gpu_name"] == "RTX Test 4070"


def test_missing_pynvml_is_silent_none():
    m = SystemMonitor(psutil_module=FakePsutil)
    # test ortamında pynvml yoktur ya da sürücü yoktur → sessizce None
    s = m.sample()
    assert set(s) == KEYS and (s["gpu"] is None or isinstance(s["gpu"], float))
    broken = SystemMonitor(nvml=types.SimpleNamespace(nvmlInit=lambda: (_ for _ in ()).throw(RuntimeError("no driver"))),
                           psutil_module=FakePsutil)
    assert broken.sample()["gpu"] is None and broken.gpu_available is False


def test_nvml_failure_mid_run_disables_gpu_without_raising():
    m = SystemMonitor(nvml=FakeNvml(fail_after=1), psutil_module=FakePsutil)
    assert m.sample()["gpu"] == 61.0
    assert m.sample()["gpu"] is None and m.sample()["gpu"] is None and not m.gpu_available


def test_missing_psutil_gives_none_not_crash():
    s = SystemMonitor(psutil_module=False).sample()
    assert s["cpu"] is None and s["ram_total"] is None and s["disk_free"] is None


def test_psutil_errors_are_swallowed():
    class Bad:
        def cpu_percent(self, interval=None): raise OSError("x")
        def virtual_memory(self): raise OSError("x")
        def disk_usage(self, p): raise OSError("x")
    s = SystemMonitor(psutil_module=Bad()).sample("/nonexistent")
    assert s["cpu"] is None and s["disk_free"] is None


def test_api_ping_record_and_expiry(monkeypatch):
    m = SystemMonitor(psutil_module=FakePsutil, ping_ttl=10)
    assert m.sample()["api_ping"] is None
    m.record_ping(182.4)
    assert m.sample()["api_ping"] == 182.4
    import kadraj.core.sysmon as mod
    base = mod.time.monotonic()
    monkeypatch.setattr(mod.time, "monotonic", lambda: base + 11)
    assert m.sample()["api_ping"] is None                       # bayat ping gösterilmez
    m.record_ping(None)
    assert m.sample()["api_ping"] is None
