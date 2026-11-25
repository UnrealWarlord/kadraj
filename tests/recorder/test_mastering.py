"""Stüdyo mastering: filtre zinciri, gerçek FFmpeg ile -14 LUFS, orijinal korunur, iptal/hata yolları."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
import threading

import numpy as np
import pytest
import soundfile as sf

from kadraj.core.contracts import SmartError
from kadraj.modules.recorder import mastering as M
from kadraj.modules.recorder.dsp import measure_loudness
from tests.recorder.conftest import SR, speechlike

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg kurulu değil")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_wav(path, seconds=10, amp=0.03, sr=SR, ch=1, seed=1):
    x = speechlike(seconds, amp=amp, sr=sr, seed=seed)
    data = np.stack([x, np.roll(x, 7)], axis=1) if ch == 2 else x
    sf.write(path, data, sr, subtype="PCM_24")
    return path


# ---- zincir üretimi -----------------------------------------------------------------------------------------------
def test_chain_has_the_required_stages_in_order():
    chain = M.build_chain(M.MasterSettings())
    order = ["highpass", "equalizer", "highshelf", "acompressor", "deesser"]
    pos = [chain.index(name) for name in order]
    assert pos == sorted(pos) and "loudnorm" not in chain
    full = M.build_filter(M.MasterSettings())
    assert full.index("deesser") < full.index("loudnorm") < full.index("alimiter")
    assert "I=-14" in full and "TP=-1" in full                                          # -14 LUFS hedefi


def test_second_pass_uses_measured_values_in_linear_mode():
    measured = {"input_i": "-30.1", "input_tp": "-9.2", "input_lra": "4.1", "input_thresh": "-40.7", "target_offset": "0.3"}
    ln = M.build_loudnorm(M.MasterSettings(), measured)
    assert "measured_I=-30.1" in ln and "linear=true" in ln and "offset=0.3" in ln and "print_format=summary" in ln
    assert "print_format=json" in M.build_loudnorm(M.MasterSettings())


def test_settings_are_clamped():
    s = M.MasterSettings(target_lufs=+5, true_peak_db=3, comp_ratio=0, deess_intensity=9, lra=100).clamped()
    assert s.target_lufs == -5 and s.true_peak_db == -0.1 and s.comp_ratio == 1 and s.deess_intensity == 1 and s.lra == 20
    assert "I=-30" in M.build_loudnorm(M.MasterSettings(target_lufs=-99))


def test_parse_loudnorm_json_variants():
    text = 'noise...\n{\n "input_i" : "-30.5",\n "input_tp" : "-8.0",\n "input_lra" : "3.0",\n "input_thresh" : "-41.0",\n "target_offset" : "0.1"\n}\n'
    assert M.parse_loudnorm_json(text)["input_i"] == "-30.5"
    for bad in ("", "no json here", text.replace("-30.5", "-inf")):
        with pytest.raises(ValueError):
            M.parse_loudnorm_json(bad)


def test_output_path_never_overwrites(tmp_path):
    src = tmp_path / "Kayit_1.wav"
    src.write_bytes(b"x")
    a = M.output_path(src)
    assert a.name == "Kayit_1_master.wav" and a.suffix == ".wav"
    a.write_bytes(b"y")
    assert M.output_path(src).name == "Kayit_1_master2.wav"


# ---- gerçek FFmpeg ----------------------------------------------------------------------------------------------------------
@needs_ffmpeg
def test_masters_to_minus_14_lufs_and_keeps_the_original(tmp_path):
    src = make_wav(tmp_path / "ham.wav", amp=0.03)
    before = sha(src)
    before_loud = measure_loudness(sf.read(src, dtype="float32")[0], SR).integrated
    assert before_loud < -26                                                               # kısık kayıt
    prog = []
    res = M.master_file(src, on_progress=prog.append)
    assert res.path == tmp_path / "ham_master.wav" and res.path.exists()
    assert sha(src) == before                                                              # orijinal bayt-bayt aynı
    info = sf.info(res.path)
    assert (info.subtype, info.samplerate, info.channels) == ("PCM_24", SR, 1) and info.format in ("WAV", "WAVEX")
    assert info.duration == pytest.approx(sf.info(src).duration, abs=0.05)
    out, _ = sf.read(res.path, dtype="float32")
    snap = measure_loudness(out, SR)
    assert snap.integrated == pytest.approx(-14.0, abs=0.8)
    assert snap.true_peak_db <= -0.7                                                       # limiter tavanı
    assert prog == sorted(prog) and prog[-1] == 1.0 and len(prog) >= 3
    assert "input_i" in res.measured and res.duration_s == pytest.approx(10.0, abs=0.05)
    assert not [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]                    # geçici dosya yok


@needs_ffmpeg
def test_target_lufs_is_configurable_and_second_run_gets_a_new_name(tmp_path):
    src = make_wav(tmp_path / "a.wav", amp=0.05)
    r1 = M.master_file(src, settings=M.MasterSettings(target_lufs=-16.0))
    r2 = M.master_file(src, settings=M.MasterSettings(target_lufs=-16.0))
    assert r1.path.name == "a_master.wav" and r2.path.name == "a_master2.wav"
    out, _ = sf.read(r1.path, dtype="float32")
    assert measure_loudness(out, SR).integrated == pytest.approx(-16.0, abs=0.8)


@needs_ffmpeg
@pytest.mark.parametrize("sr, ch", [(44100, 1), (96000, 2)])
def test_keeps_sample_rate_and_channels(tmp_path, sr, ch):
    src = make_wav(tmp_path / "x.wav", seconds=6, amp=0.04, sr=sr, ch=ch)
    res = M.master_file(src)
    info = sf.info(res.path)
    assert info.samplerate == sr and info.channels == ch and info.subtype == "PCM_24"


@needs_ffmpeg
def test_cancellation_removes_partial_output_and_keeps_original(tmp_path):
    src = make_wav(tmp_path / "c.wav", seconds=30)
    before = sha(src)
    flag = {"n": 0}

    def cancelled():
        flag["n"] += 1
        return flag["n"] > 2
    with pytest.raises(M.MasterCancelled):
        M.master_file(src, is_cancelled=cancelled)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.wav"] and sha(src) == before


@needs_ffmpeg
def test_silent_file_is_a_clear_error_not_a_crash(tmp_path):
    src = tmp_path / "s.wav"
    sf.write(src, np.zeros(SR * 3, dtype=np.float32), SR, subtype="PCM_24")
    with pytest.raises(SmartError):
        M.master_file(src)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["s.wav"]


@needs_ffmpeg
def test_corrupt_input_is_a_smart_error(tmp_path):
    src = tmp_path / "bad.wav"
    src.write_bytes(b"RIFF....not a real wav file")
    with pytest.raises(SmartError):
        M.master_file(src)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bad.wav"]


# ---- FFmpeg olmadan / sahte süreçle ---------------------------------------------------------------------------------------------
def test_missing_ffmpeg_is_a_smart_error(tmp_path, monkeypatch):
    src = make_wav(tmp_path / "a.wav", seconds=1)
    monkeypatch.setattr(M, "find_ffmpeg", lambda: None)
    with pytest.raises(SmartError) as ei:
        M.master_file(src)
    assert ei.value.code == "tool.missing" and "ffmpeg" in ei.value.title.lower()


def test_missing_input_file_is_a_smart_error(tmp_path):
    with pytest.raises(SmartError) as ei:
        M.master_file(tmp_path / "yok.wav", ffmpeg="/bin/true")
    assert ei.value.code == "fs.not_found"


class FakePopen:
    """FFmpeg yerine geçen sahte süreç: ``script`` (stdout satırları, dönüş kodu, stderr) sırayla verilir."""

    runs = []

    def __init__(self, cmd, stdout=None, stderr=None, stdin=None, text=None):
        self.cmd = cmd
        FakePopen.runs.append(cmd)
        script = FakePopen.script.pop(0)
        self._lines, self.rc, err = script
        if stderr is not None:
            stderr.write(err)
            stderr.flush()
        self.stdout = iter(self._lines)
        self.terminated = False

    def wait(self, timeout=None):
        return self.rc

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminated = True

    def kill(self):
        pass


LOUDNORM = ('{\n "input_i" : "-30.00",\n "input_tp" : "-10.00",\n "input_lra" : "3.00",\n "input_thresh" : "-40.00",\n'
            ' "target_offset" : "0.00"\n}\n')


def test_two_pass_command_lines_with_a_fake_ffmpeg(tmp_path):
    src = make_wav(tmp_path / "a.wav", seconds=2)
    FakePopen.runs, FakePopen.script = [], [([], 0, LOUDNORM), ([], 0, "")]
    out_tmp = []

    class Writing(FakePopen):
        def __init__(self, cmd, **kw):
            super().__init__(cmd, **kw)
            if "-y" in cmd:
                Path(cmd[-1]).write_bytes(b"RIFFfake")
                out_tmp.append(cmd[-1])
    res = M.master_file(src, ffmpeg="ffmpeg", popen=Writing)
    first, second = FakePopen.runs
    assert first[-3:] == ["-f", "null", "-"] and "-y" not in first and "print_format=json" in " ".join(first)
    assert "-y" in second and "linear=true" in " ".join(second) and "measured_I=-30.00" in " ".join(second)
    assert second[second.index("-c:a") + 1] == "pcm_s24le" and second[second.index("-ar") + 1] == str(SR)
    assert second[second.index("-f", second.index("-c:a")) + 1] == "wav" and str(res.path).endswith("_master.wav")
    assert res.path.read_bytes() == b"RIFFfake" and not [p for p in tmp_path.iterdir() if ".tmp" in p.name]


def test_ffmpeg_failure_goes_through_the_error_catalog(tmp_path):
    src = make_wav(tmp_path / "a.wav", seconds=1)
    FakePopen.runs, FakePopen.script = [], [([], 1, "Error while decoding stream #0:0: Invalid data found")]
    with pytest.raises(SmartError) as ei:
        M.master_file(src, ffmpeg="ffmpeg", popen=FakePopen)
    assert ei.value.title and ei.value.steps and "Invalid data" in ei.value.technical
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.wav"]


def test_progress_lines_are_mapped_into_the_two_pass_ranges(tmp_path):
    src = make_wav(tmp_path / "a.wav", seconds=10)
    FakePopen.runs = []
    FakePopen.script = [(["out_time_us=5000000\n", "out_time_us=10000000\n"], 0, LOUDNORM),
                        (["out_time_us=5000000\n", "out_time_us=10000000\n"], 0, "")]
    prog = []

    class Writing(FakePopen):
        def __init__(self, cmd, **kw):
            super().__init__(cmd, **kw)
            if "-y" in cmd:
                Path(cmd[-1]).write_bytes(b"x")
    M.master_file(src, ffmpeg="ffmpeg", popen=Writing, on_progress=prog.append)
    assert prog[:2] == [pytest.approx(0.2), pytest.approx(0.4)] and prog[2:4] == [pytest.approx(0.7), pytest.approx(1.0)]
    assert prog[-1] == 1.0


def test_mastering_never_writes_non_wav_output(tmp_path):
    src = make_wav(tmp_path / "a.wav", seconds=1)
    assert M.output_path(src).suffix == ".wav" and M.output_path(tmp_path / "x.mp3").suffix == ".wav"
