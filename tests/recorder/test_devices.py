"""Aygıt katmanı: PortAudio hata eşlemesi, aygıt listesi, backend yokluğu (Docker/CI)."""
from __future__ import annotations

import errno
import sys

import pytest

from kadraj.core.contracts import SmartError
from kadraj.modules.recorder import devices as D


class FakeSD:
    def __init__(self, devs, default=1, fail=None):
        self._devs, self.default, self.fail = devs, type("Def", (), {"device": (default, 0)})(), fail

    def query_devices(self):
        if self.fail:
            raise self.fail
        return self._devs

    def check_input_settings(self, **kw):
        if self.fail:
            raise self.fail


DEVS = [{"name": "HDMI", "max_input_channels": 0, "default_samplerate": 44100.0},
        {"name": "USB Mic", "max_input_channels": 2, "default_samplerate": 48000.0},
        {"name": "Dahili", "max_input_channels": 1, "default_samplerate": 44100.0}]


def test_lists_only_input_devices_and_marks_default():
    out = D.list_input_devices(FakeSD(DEVS, default=1))
    assert [(d.index, d.name, d.is_default) for d in out] == [(1, "USB Mic", True), (2, "Dahili", False)]
    assert out[0].max_input_channels == 2 and out[0].default_samplerate == 48000.0 and out[0].label == "USB Mic"


def test_no_input_devices_is_an_empty_list_not_an_error():
    assert D.list_input_devices(FakeSD([DEVS[0]])) == []
    assert D.list_input_devices(FakeSD([])) == []


def test_missing_default_info_does_not_crash():
    class NoDefault(FakeSD):
        default = None
    assert len(D.list_input_devices(NoDefault(DEVS))) == 2


@pytest.mark.parametrize("message, code", [
    ("Error opening InputStream: Invalid sample rate [PaErrorCode -9997]", "audio.unsupported_rate"),
    ("Invalid number of channels [PaErrorCode -9998]", "audio.unsupported_channels"),
    ("Device unavailable [PaErrorCode -9985]", "audio.device_busy"),
    ("Error querying device -1 [PaErrorCode -9996]", "audio.no_input"),
    ("No Default Input Device Available", "audio.no_input"),
    ("Invalid device", "audio.no_input"),
    ("PortAudio library not found", "audio.no_backend"),
    ("Permission denied", "audio.permission"),
    ("something weird", "audio.open_failed"),
])
def test_portaudio_errors_map_to_plain_cards(message, code):
    err = D.map_audio_error(RuntimeError(message))
    assert isinstance(err, SmartError) and err.code == code
    assert err.title and err.summary and err.cause and err.steps and err.technical == f"RuntimeError: {message}"
    assert err.source == "recorder"


def test_error_mapping_edge_cases():
    smart = SmartError("x", "t")
    assert D.map_audio_error(smart) is smart
    assert D.map_audio_error(PermissionError("denied")).code == "audio.permission"
    assert D.map_audio_error(OSError(errno.ENOSPC, "full")).code == "disk.full"
    assert D.map_audio_error(ImportError("no module")).code == "audio.no_backend"
    assert D.map_audio_error(RuntimeError("busy")).code == "audio.device_busy"
    assert D.map_audio_error(RuntimeError("x")).retryable


def test_backend_failure_while_listing_is_a_smart_error():
    with pytest.raises(SmartError) as ei:
        D.list_input_devices(FakeSD([], fail=OSError("PortAudio library not found")))
    assert ei.value.code == "audio.no_backend"


def test_missing_sounddevice_module_is_a_smart_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)                   # import → ImportError
    with pytest.raises(SmartError) as ei:
        D.list_input_devices()
    assert ei.value.code == "audio.no_backend"
    with pytest.raises(SmartError):
        D.default_stream_factory(48000, 1, None, 1024, lambda *a: None)


def test_check_input_returns_none_or_a_card():
    assert D.check_input(1, 48000, 1, FakeSD(DEVS)) is None
    bad = D.check_input(1, 12345, 1, FakeSD(DEVS, fail=RuntimeError("Invalid sample rate [PaErrorCode -9997]")))
    assert bad is not None and bad.code == "audio.unsupported_rate"


def test_sample_rate_constants_follow_the_plan():
    assert D.SAMPLE_RATES == (44100, 48000, 88200, 96000) and D.SIMPLE_RATE == 48000
