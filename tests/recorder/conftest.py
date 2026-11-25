"""Ses Kayıt Stüdyosu test düzeni: gerçek mikrofon yok (sahte akış), gerçek ağ yok, modül dizeleri yüklü."""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from kadraj.core import i18n
from kadraj.modules.recorder.devices import InputDevice
from kadraj.modules.recorder.engine import RecorderEngine
from kadraj.modules.recorder.strings import STRINGS

SR = 48000


@pytest.fixture(autouse=True)
def _recorder_strings():
    i18n.register_strings(STRINGS)
    i18n.set_language("tr")
    yield


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = []

    def blocked(self, *a, **k):
        attempts.append(a)
        raise OSError("ağ kapalı (test)")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("dns kapalı")))
    return attempts


class FakeStream:
    def __init__(self, cb, owner):
        self.cb, self.owner, self.active, self.closed = cb, owner, False, False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.closed = True


class FakeMic:
    """Sahte mikrofon: ``factory`` motora verilir, ``feed`` ses geri çağrısını doğrudan çağırır."""

    def __init__(self):
        self.stream = None
        self.opened = []                       # (sr, channels, device, blocksize)
        self.fail_with = None

    def factory(self, sr, ch, device, blocksize, cb):
        if self.fail_with is not None:
            raise self.fail_with
        self.opened.append((sr, ch, device, blocksize))
        self.stream = FakeStream(cb, self)
        return self.stream

    def feed(self, seconds, amp=0.3, freq=220.0, sr=SR, ch=1, block=1024, modulate=True, noise=0.0, seed=0):
        n = int(seconds * sr)
        t = np.arange(n) / sr
        sig = amp * np.sin(2 * np.pi * freq * t)
        if modulate:
            sig = sig * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
        if noise:
            sig = sig + noise * np.random.default_rng(seed).standard_normal(n)
        sig = sig.astype(np.float32)
        for i in range(0, n, block):
            b = np.repeat(sig[i:i + block, None], ch, axis=1)
            self.stream.cb(b, len(b), None, None)

    def feed_array(self, x, block=1024):
        x = np.asarray(x, dtype=np.float32)
        if x.ndim == 1:
            x = x[:, None]
        for i in range(0, len(x), block):
            b = x[i:i + block]
            self.stream.cb(b, len(b), None, None)


@pytest.fixture()
def mic():
    return FakeMic()


@pytest.fixture()
def engine(mic):
    e = RecorderEngine(mic.factory)
    yield e
    e.stop()


def speechlike(seconds, amp=0.1, sr=SR, seed=1, noise=0.01):
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    env = (0.5 + 0.5 * np.sin(2 * np.pi * 0.9 * t)).clip(0.03, 1) ** 2
    x = (np.sin(2 * np.pi * 180 * t) + 0.5 * np.sin(2 * np.pi * 540 * t)) * env * amp + noise * rng.standard_normal(n)
    return x.astype(np.float32)


@pytest.fixture()
def svc(ctx, mic, tmp_path):
    """Açık projeli bağlam + sahte mikrofonlu ``RecorderService`` (``ctx.service('recorder')``)."""
    ctx.create_project("Ses", tmp_path / "projeler")
    service = ctx.service("recorder")
    assert service is not None, ctx.registry.errors
    service.engine = RecorderEngine(mic.factory)
    service._injected_factory = True
    service._device_lister = lambda: [InputDevice(0, "Sahte Mikrofon", 2, 48000.0, True)]
    service._devices = None
    service.mic = mic
    yield service
    service.stop()
