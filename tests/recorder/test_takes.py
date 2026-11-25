"""Kayıt listesi: dosya adı, tarama, dalga formu özeti, srt/master bağlantıları."""
from __future__ import annotations

import json
import os
import time

import numpy as np
import pytest
import soundfile as sf

from kadraj.modules.recorder import takes as T
from tests.recorder.conftest import SR


def wav(path, seconds=1.0, sr=SR, ch=1, amp=0.3):
    t = np.arange(int(seconds * sr)) / sr
    x = (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(path, np.repeat(x[:, None], ch, axis=1) if ch > 1 else x, sr, subtype="PCM_24")
    return path


def test_new_take_path_is_a_unique_wav(tmp_path):
    a = T.new_take_path(tmp_path, now=1_800_000_000)
    assert a.suffix == ".wav" and a.name.startswith("Kayit_") and a.parent == tmp_path
    a.write_bytes(b"x")
    b = T.new_take_path(tmp_path, now=1_800_000_000)
    assert b != a and b.name.endswith("_2.wav") and b.suffix == ".wav"


def test_scan_lists_wavs_newest_first_with_metadata(tmp_path):
    old = wav(tmp_path / "eski.wav", 1.0, 44100)
    os.utime(old, (1_700_000_000, 1_700_000_000))
    new = wav(tmp_path / "yeni.wav", 2.0, 48000, ch=2)
    (tmp_path / "not.mp3").write_bytes(b"x")
    (tmp_path / "readme.txt").write_text("x")
    takes = T.scan_takes(tmp_path)
    assert [t.name for t in takes] == ["yeni", "eski"]
    t0 = takes[0]
    assert (t0.sample_rate, t0.channels) == (48000, 2) and t0.duration_s == pytest.approx(2.0, abs=0.01)
    assert t0.size_bytes == new.stat().st_size and t0.srt_path is None and not t0.has_srt and not t0.is_master


def test_scan_ignores_temp_corrupt_and_missing(tmp_path):
    wav(tmp_path / "iyi.wav")
    (tmp_path / "bozuk.wav").write_bytes(b"RIFFxxxxWAVEgarbage")
    (tmp_path / ".gizli.tmp.wav").write_bytes(b"x")
    (tmp_path / "a.tmp.wav").write_bytes(b"x")
    assert [t.name for t in T.scan_takes(tmp_path)] == ["iyi"]
    assert T.scan_takes(tmp_path / "yok") == []


def test_srt_and_master_links(tmp_path):
    w = wav(tmp_path / "Kayit_1.wav")
    assert T.srt_for(w) == tmp_path / "Kayit_1.srt"
    (tmp_path / "Kayit_1.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n")
    m = wav(tmp_path / "Kayit_1_master.wav")
    takes = {t.name: t for t in T.scan_takes(tmp_path)}
    assert takes["Kayit_1"].has_srt and takes["Kayit_1"].master_path == m and not takes["Kayit_1"].is_master
    assert takes["Kayit_1_master"].is_master and takes["Kayit_1_master"].master_path is None


def test_waveform_peaks_shape_and_values(tmp_path):
    t = np.arange(SR * 2) / SR
    x = np.where(t < 1.0, 0.1, 0.8).astype(np.float32)                              # yarı sessiz, yarı yüksek
    sf.write(tmp_path / "w.wav", x, SR, subtype="PCM_24")
    peaks = T.waveform_peaks(tmp_path / "w.wav", buckets=100)
    assert len(peaks) == 100 and all(0 <= p <= 1 for p in peaks)
    assert np.mean(peaks[:45]) == pytest.approx(0.1, abs=0.01) and np.mean(peaks[55:]) == pytest.approx(0.8, abs=0.01)


def test_waveform_peaks_cache_roundtrip(tmp_path):
    p = wav(tmp_path / "c.wav", 1.0)
    cache = tmp_path / "cache" / "wf"
    first = T.waveform_peaks(p, 50, cache)
    files = list(cache.glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text()) == first
    files[0].write_text(json.dumps([0.5] * 50))                                      # önbellek gerçekten okunuyor
    assert T.waveform_peaks(p, 50, cache) == [0.5] * 50
    files[0].write_text("bozuk")                                                      # bozuk önbellek yeniden hesaplanır
    assert len(T.waveform_peaks(p, 50, cache)) == 50


def test_waveform_of_empty_and_tiny_files(tmp_path):
    sf.write(tmp_path / "e.wav", np.zeros(0, dtype=np.float32), SR, subtype="PCM_24")
    assert T.waveform_peaks(tmp_path / "e.wav", 10) == [0.0] * 10
    sf.write(tmp_path / "t.wav", np.full(5, 0.5, dtype=np.float32), SR, subtype="PCM_24")
    assert len(T.waveform_peaks(tmp_path / "t.wav", 10)) == 10
