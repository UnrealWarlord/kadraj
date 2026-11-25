"""Akıllı Hata: translate() sırası, genel kurallar, sözlük kancası, asla istisna atmama."""
from __future__ import annotations

import errno
import socket
import sys
import types
import urllib.error

import pytest

from kadraj.core import errors
from kadraj.core.contracts import ErrorRule, Severity, SmartError


@pytest.fixture(autouse=True)
def _reset():
    errors.reset_catalog_cache()
    yield
    errors.reset_catalog_cache()
    sys.modules.pop("kadraj.core.error_catalog", None)
    errors._extra_rules.clear()


def test_smart_error_passes_through():
    e = SmartError("x", "t")
    assert errors.translate(e) is e


@pytest.mark.parametrize("exc", [ConnectionRefusedError("x"), socket.gaierror("dns"),
                                 urllib.error.URLError(OSError("no route"))])
def test_network_errors_become_offline_card(exc):
    err = errors.translate(exc, {"source": "downloader"})
    assert err.code == "net.offline" and err.retryable and err.severity is Severity.WARNING
    assert err.source == "downloader" and err.steps and err.actions[0].kind == "retry"
    assert err.technical


def test_timeout_card():
    assert errors.translate(TimeoutError("slow")).code == "net.timeout"
    assert errors.translate(socket.timeout("slow")).code == "net.timeout"


def test_disk_full_card():
    assert errors.translate(OSError(errno.ENOSPC, "No space left")).code == "disk.full"


def test_permission_and_not_found():
    assert errors.translate(PermissionError(13, "denied", "/x/y")).code == "fs.permission"
    nf = errors.translate(FileNotFoundError(2, "nope", "/a/b.mp4"))
    assert nf.code == "fs.not_found" and "/a/b.mp4" in nf.summary
    tool = errors.translate(FileNotFoundError("ffmpeg"), {"tool": "ffmpeg"})
    assert tool.code == "tool.missing" and "ffmpeg" in tool.title
    assert tool.actions[0].kind == "open_settings"


def test_unknown_exception_gets_generic_card_not_a_crash():
    err = errors.translate(RuntimeError("boom"))
    assert err.code == "app.unexpected" and "boom" in err.technical
    assert any(a.kind == "copy" for a in err.actions)


def test_catalog_rules_win_over_builtins_and_priority_orders(monkeypatch):
    mod = types.ModuleType("kadraj.core.error_catalog")
    mod.RULES = [
        ErrorRule("late", lambda e, c: True, lambda e, c: SmartError("late", "L"), priority=500),
        ErrorRule("early", lambda e, c: isinstance(e, ConnectionError),
                  lambda e, c: SmartError("catalog.net", "Katalog"), priority=10),
    ]
    monkeypatch.setitem(sys.modules, "kadraj.core.error_catalog", mod)
    assert errors.translate(ConnectionError("x")).code == "catalog.net"
    assert errors.translate(ValueError("x")).code == "late"


def test_broken_catalog_rule_is_skipped(monkeypatch):
    mod = types.ModuleType("kadraj.core.error_catalog")
    mod.RULES = [ErrorRule("bad", lambda e, c: 1 / 0, lambda e, c: SmartError("x", "x"), priority=1),
                 ErrorRule("bad2", lambda e, c: True, lambda e, c: "not a SmartError", priority=2),
                 "garbage-entry"]
    monkeypatch.setitem(sys.modules, "kadraj.core.error_catalog", mod)
    assert errors.translate(ConnectionError("x")).code == "net.offline"      # yedek kurala düştü


def test_register_rule_at_runtime():
    errors.register_rule(ErrorRule("mine", lambda e, c: isinstance(e, KeyError),
                                   lambda e, c: SmartError("mine", "Benim"), priority=1))
    assert errors.translate(KeyError("k")).code == "mine"


def test_missing_catalog_module_is_fine():
    sys.modules.pop("kadraj.core.error_catalog", None)
    assert errors.translate(ConnectionError("x")).code == "net.offline"
