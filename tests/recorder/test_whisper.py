"""Yerel konuşma tanıma sağlayıcısı: ağır model sahte yükleyiciyle; eksik paket, çevrimdışı model, iptal, ilerleme."""
from __future__ import annotations

import sys
import types

import pytest

from kadraj.core.contracts import SmartError
from kadraj.ai_engine.providers.whisper_local import (INSTALL_COMMAND, MODELS, Segment, TranscribeCancelled,
                                                      WhisperLocalProvider)


class FakeSeg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class FakeModel:
    def __init__(self, segs, duration=10.0, language="tr"):
        self.segs, self.duration, self.language, self.calls = segs, duration, language, []

    def transcribe(self, path, **kw):
        self.calls.append((path, kw))
        return iter(self.segs), types.SimpleNamespace(duration=self.duration, language=self.language)


def provider_with(model, record=None):
    def loader(size, local_only):
        if record is not None:
            record.append((size, local_only))
        return model
    return WhisperLocalProvider(model_loader=loader)


def test_transcribes_segments_and_reports_progress(tmp_path):
    model = FakeModel([FakeSeg(0, 2, " Merhaba "), FakeSeg(2, 5, "dünya"), FakeSeg(5, 6, "  ")], duration=10)
    prog = []
    out = provider_with(model).transcribe(tmp_path / "a.wav", on_progress=prog.append)
    assert [(s.start, s.end, s.text) for s in out.segments] == [(0, 2, "Merhaba"), (2, 5, "dünya")]
    assert out.language == "tr" and out.duration == 10.0
    assert prog == sorted(prog) and prog[0] == pytest.approx(0.2) and prog[-1] == 1.0
    kw = model.calls[0][1]
    assert kw["language"] == "tr" and kw["vad_filter"] is True and model.calls[0][0] == str(tmp_path / "a.wav")


def test_auto_language_passes_none(tmp_path):
    model = FakeModel([FakeSeg(0, 1, "x")])
    for lang in ("auto", "", None):
        provider_with(model).transcribe(tmp_path / "a.wav", language=lang)
        assert model.calls[-1][1]["language"] is None


def test_cancellation_between_segments(tmp_path):
    model = FakeModel([FakeSeg(i, i + 1, f"s{i}") for i in range(10)])
    seen = {"n": 0}

    def cancelled():
        seen["n"] += 1
        return seen["n"] > 3
    with pytest.raises(TranscribeCancelled):
        provider_with(model).transcribe(tmp_path / "a.wav", is_cancelled=cancelled)


def test_model_is_loaded_local_first_then_cached():
    rec = []
    p = provider_with(FakeModel([]), rec)
    p.load_model("small")
    p.load_model("small")
    assert rec == [("small", True)]                                                   # çevrimdışı önbellek önce; tekrar yüklenmez


def test_download_fallback_when_not_cached():
    calls = []

    def loader(size, local_only):
        calls.append(local_only)
        if local_only:
            raise OSError("model not in cache")
        return FakeModel([])
    p = WhisperLocalProvider(model_loader=loader)
    assert p.load_model("tiny") is not None and calls == [True, False]


def test_offline_model_download_is_a_smart_error_with_plain_steps():
    def loader(size, local_only):
        raise ConnectionError("Cannot reach huggingface.co")
    p = WhisperLocalProvider(model_loader=loader)
    with pytest.raises(SmartError) as ei:
        p.load_model("small")
    e = ei.value
    assert e.code == "whisper.whisper_model" and e.retryable and "internet" in (e.cause + e.steps[0]).lower()
    assert e.source == "whisper_local"


def test_no_download_allowed_fails_fast_when_not_cached():
    calls = []

    def loader(size, local_only):
        calls.append(local_only)
        raise OSError("not cached")
    p = WhisperLocalProvider(model_loader=loader)
    with pytest.raises(SmartError) as ei:
        p.load_model("small", allow_download=False)
    assert calls == [True] and ei.value.retryable


def test_missing_package_is_a_smart_error_with_an_install_action(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)                          # import edilemez
    p = WhisperLocalProvider()
    assert p.available() is False
    with pytest.raises(SmartError) as ei:
        p.transcribe(tmp_path / "a.wav")
    e = ei.value
    assert e.code == "whisper.whisper_missing" and e.actions and e.actions[0].kind == "copy"
    assert e.actions[0].payload == {"text": INSTALL_COMMAND} and "paketi kur" in e.actions[0].label
    assert "faster-whisper" in INSTALL_COMMAND


def test_unexpected_model_errors_are_translated_not_raised_raw(tmp_path):
    class Boom:
        def transcribe(self, *a, **k):
            raise RuntimeError("CUDA out of memory")
    p = provider_with(Boom())
    with pytest.raises(SmartError):
        p.transcribe(tmp_path / "a.wav")


def test_test_connection_reflects_package_presence(monkeypatch):
    p = WhisperLocalProvider()
    monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object() if name == "faster_whisper" else None)
    assert p.test_connection().ok and p.id == "whisper_local" and not p.needs_key and p.local
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    res = p.test_connection()
    assert not res.ok and res.error.code == "provider.module_missing"


def test_models_list():
    assert MODELS == ("tiny", "base", "small", "medium", "large-v3")
    assert isinstance(Segment(0, 1, "x"), Segment)
