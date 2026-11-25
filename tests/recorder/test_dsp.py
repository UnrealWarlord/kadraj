"""DSP çekirdeği: kapı, VU, BS.1770 yüksek ses ölçümü, mikrofon testi/auto-gain."""
from __future__ import annotations

import numpy as np
import pytest

from kadraj.modules.recorder import dsp
from kadraj.modules.recorder.dsp import (FLOOR_DB, GateParams, LevelMeter, LoudnessMeter, NoiseGate, analyze_level,
                                         db_to_lin, lin_to_db, measure_loudness)
from tests.recorder.conftest import SR, speechlike


def sine(seconds, amp, freq=1000.0, sr=SR, ch=1):
    t = np.arange(int(seconds * sr)) / sr
    x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.repeat(x[:, None], ch, axis=1) if ch > 1 else x


def run_gate(gate, x, block=1024):
    return np.concatenate([gate.process(x[i:i + block]) for i in range(0, len(x), block)])


def rms_db(x):
    return lin_to_db(float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2))))


# ---- dönüşümler / parametreler -------------------------------------------------------------------------------
def test_db_conversions():
    assert lin_to_db(1.0) == 0.0 and lin_to_db(0.5) == pytest.approx(-6.0206, abs=1e-3)
    assert lin_to_db(0.0) == FLOOR_DB and db_to_lin(-20) == pytest.approx(0.1)


def test_gate_params_are_clamped():
    p = GateParams(threshold_db=50, attack_ms=-3, hold_ms=1e6, release_ms=0, range_db=20, hysteresis_db=99).clamped()
    assert p.threshold_db == 0 and p.attack_ms == 0.1 and p.hold_ms == 2000 and p.release_ms == 1
    assert p.range_db == 0 and p.hysteresis_db == 12
    assert dsp.SIMPLE_GATE == dsp.SIMPLE_GATE.clamped()                       # Basit ön ayar geçerli aralıkta


# ---- gürültü kapısı -------------------------------------------------------------------------------------------------
def test_disabled_gate_is_bit_exact_passthrough():
    g = NoiseGate(SR)
    x = speechlike(0.5)
    y = g.process(x)
    assert np.array_equal(x, y) and y is not x and g.state == "bypass" and g.gr_db == 0.0


def test_gate_does_not_modify_its_input():
    g = NoiseGate(SR, GateParams(), enabled=True)
    x = speechlike(0.3)
    before = x.copy()
    g.process(x)
    assert np.array_equal(x, before)


def test_gate_attenuates_noise_below_threshold_by_the_range():
    g = NoiseGate(SR, GateParams(threshold_db=-40, range_db=-40, release_ms=50, hold_ms=0), enabled=True)
    noise = (0.001 * np.random.default_rng(0).standard_normal(SR * 2)).astype(np.float32)      # ≈ -60 dBFS RMS, tepe ≈ -49
    y = run_gate(g, noise)
    tail = y[SR:]                                                              # kapı tamamen kapandıktan sonra
    drop = rms_db(noise[SR:]) - rms_db(tail)
    assert 36 <= drop <= 44 and g.state == "closed" and 38 <= g.gr_db <= 41


def test_gate_passes_loud_signal_and_opens_quickly():
    g = NoiseGate(SR, GateParams(threshold_db=-40, attack_ms=2), enabled=True)
    quiet = np.zeros(SR // 2, dtype=np.float32)
    loud = sine(0.5, 0.3)
    run_gate(g, quiet)
    assert g.state == "closed"
    y = g.process(loud[:2048])
    assert g.state == "open"
    settled = run_gate(g, loud)
    assert rms_db(settled[SR // 4:]) == pytest.approx(rms_db(loud[SR // 4:]), abs=0.3)      # açıkken şeffaf
    assert g.gr_db == 0.0


def test_gate_hold_keeps_it_open_after_the_signal_stops():
    g = NoiseGate(SR, GateParams(threshold_db=-40, hold_ms=300, release_ms=100), enabled=True)
    g.process(sine(0.1, 0.3))
    assert g.state == "open"
    g.process(np.zeros(int(SR * 0.1), dtype=np.float32))                       # 100 ms sessizlik < tutma 300 ms
    assert g.state == "hold"
    g.process(np.zeros(int(SR * 0.4), dtype=np.float32))                       # toplam 500 ms > 300 ms
    assert g.state == "closed"


def test_gate_hysteresis_prevents_chatter():
    p = GateParams(threshold_db=-30, hysteresis_db=6, hold_ms=0, release_ms=10)
    g = NoiseGate(SR, p, enabled=True)
    g.process(sine(0.05, db_to_lin(-20)))                                      # açar
    assert g.state == "open"
    g.process(sine(0.05, db_to_lin(-33)))                                      # eşiğin altında ama histerezis içinde
    assert g.state == "open"
    g.process(sine(0.05, db_to_lin(-40)))                                      # histerezisin de altı
    assert g.state in ("closed", "hold")


def test_gate_is_independent_of_block_size():
    x = np.concatenate([sine(0.3, 0.3), np.zeros(SR // 2, dtype=np.float32), sine(0.3, 0.2)])
    out = []
    for block in (256, 1024, 4096):
        g = NoiseGate(SR, GateParams(threshold_db=-40), enabled=True)
        out.append(run_gate(g, x, block))
    for a in out[1:]:
        assert np.max(np.abs(a - out[0])) < 0.05                              # alt-blok kazançları ≈ aynı (sınırlar kayar)


def test_gate_live_param_and_enable_changes():
    g = NoiseGate(SR, GateParams(threshold_db=-60), enabled=True)
    noise = (0.003 * np.random.default_rng(2).standard_normal(SR)).astype(np.float32)
    run_gate(g, noise)
    assert g.state in ("open", "hold")                                          # -50 dB gürültü eşik -60 üstünde
    g.set_params(GateParams(threshold_db=-30, hold_ms=0, release_ms=20))
    run_gate(g, noise)
    assert g.state == "closed"
    g.set_enabled(False)
    assert g.state == "bypass" and g.gr_db == 0
    g.set_enabled(True)
    assert g.state == "closed"


def test_gate_stereo_uses_the_loudest_channel_and_keeps_shape():
    g = NoiseGate(SR, GateParams(threshold_db=-40), enabled=True)
    x = np.zeros((SR // 2, 2), dtype=np.float32)
    x[:, 1] = sine(0.5, 0.3)
    y = g.process(x)
    assert y.shape == x.shape and g.state == "open"


# ---- VU --------------------------------------------------------------------------------------------------------------------
def test_level_meter_peak_rms_values():
    m = LevelMeter(SR, 1)
    s = m.process(sine(0.5, 0.5))
    assert s.peak_db[0] == pytest.approx(-6.02, abs=0.05) and s.rms_db[0] == pytest.approx(-9.03, abs=0.1)
    assert not s.clipping and s.max_peak_db == s.peak_db[0]


def test_level_meter_hold_then_fall():
    m = LevelMeter(SR, 1, hold_s=1.0, fall_db_per_s=20.0)
    m.process(sine(0.1, 0.8))
    held = m.snapshot.hold_db[0]
    quiet = np.full(SR // 10, 0.001, dtype=np.float32)
    for _ in range(5):                                                         # 0.5 sn: tutma süresi içinde
        m.process(quiet)
    assert m.snapshot.hold_db[0] == held
    for _ in range(20):                                                        # 2 sn daha: düşmeye başlar
        m.process(quiet)
    assert m.snapshot.hold_db[0] < held - 10


def test_level_meter_clipping_latch_and_release():
    m = LevelMeter(SR, 1, clip_hold_s=0.5)
    x = np.zeros(1024, dtype=np.float32)
    x[100] = 1.0
    s = m.process(x)
    assert s.clipping and s.clip_samples == 1
    for _ in range(40):                                                        # 0.85 sn temiz ses
        s = m.process(np.zeros(1024, dtype=np.float32))
    assert not s.clipping and s.clip_samples == 1                              # mandal bırakıldı, sayaç kalır
    m.reset()
    assert m.snapshot.clip_samples == 0


def test_level_meter_mono_source_fills_both_channels_and_silence_floor():
    m = LevelMeter(SR, 2)
    s = m.process(sine(0.2, 0.25))
    assert len(s.peak_db) == 2 and s.peak_db[0] == s.peak_db[1]
    assert LevelMeter(SR, 1).process(np.zeros(1024, dtype=np.float32)).peak_db[0] == FLOOR_DB


# ---- BS.1770 ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sr", [44100, 48000, 88200, 96000])
def test_lufs_matches_the_reference_values(sr):
    mono = measure_loudness(sine(5, 0.1, 1000, sr), sr)                        # -20 dBFS mono 1 kHz ⇒ -23.0 LUFS
    assert mono.integrated == pytest.approx(-23.0, abs=0.06)
    assert mono.momentary == pytest.approx(-23.0, abs=0.06) and mono.short_term == pytest.approx(-23.0, abs=0.06)
    stereo = measure_loudness(sine(5, 0.1, 1000, sr, ch=2), sr)                # iki kanal +3.01 dB
    assert stereo.integrated == pytest.approx(-19.99, abs=0.06)


def test_full_scale_sine_is_minus_3_01_lufs():
    assert measure_loudness(sine(3, 1.0, 997), SR).integrated == pytest.approx(-3.01, abs=0.03)


def test_k_weighting_attenuates_lows_and_boosts_highs():
    lo = measure_loudness(sine(4, 0.1, 50), SR).integrated
    mid = measure_loudness(sine(4, 0.1, 1000), SR).integrated
    hi = measure_loudness(sine(4, 0.1, 8000), SR).integrated
    assert lo < mid - 1.5 and hi > mid + 2.0


def test_integrated_loudness_gates_out_silence():
    tone = sine(4, 0.1)
    full = np.concatenate([tone, np.zeros(SR * 6, dtype=np.float32)])          # 4 sn ses + 6 sn sessizlik
    assert measure_loudness(full, SR).integrated == pytest.approx(measure_loudness(tone, SR).integrated, abs=0.3)      # geçiş pencereleri kısmi
    quiet_part = np.concatenate([sine(4, 0.1), sine(4, 0.0005)])               # -66 dBFS bölüm göreli kapıyla dışlanır
    assert measure_loudness(quiet_part, SR).integrated == pytest.approx(-23.0, abs=0.3)


def test_momentary_follows_the_last_400ms_and_short_term_the_last_3s():
    m = LoudnessMeter(SR, 1)
    for i in range(0, SR * 4, 4800):
        m.process(sine(0.1, 0.1)[:4800] if False else np.zeros(4800, dtype=np.float32))
    snap = m.process(sine(0.5, 0.3))
    assert snap.momentary > snap.short_term + 5                                # kısa süreli pencere daha yavaş yükselir


def test_true_peak_exceeds_sample_peak_for_intersample_peaks():
    sr = 48000
    t = np.arange(sr) / sr
    x = (np.sin(2 * np.pi * 12000 * t + np.pi / 4)).astype(np.float32)         # örnekler ≈ 0.707'de, gerçek tepe 1.0
    sample_peak = lin_to_db(float(np.max(np.abs(x))))
    tp = measure_loudness(x, sr).true_peak_db
    assert sample_peak < -2.5 and tp > -0.5


def test_true_peak_of_a_plain_sine_matches_its_amplitude():
    assert measure_loudness(sine(2, 0.5, 1000), SR).true_peak_db == pytest.approx(-6.02, abs=0.1)


def test_loudness_meter_reset_and_silence():
    m = LoudnessMeter(SR, 1)
    m.process(sine(2, 0.3))
    m.reset()
    assert m.snapshot.integrated == FLOOR_DB
    assert measure_loudness(np.zeros(SR * 2, dtype=np.float32), SR).integrated == FLOOR_DB


def test_loudness_meter_handles_odd_chunk_sizes_identically():
    x = speechlike(6)
    a = measure_loudness(x, SR, chunk=48000)
    b = measure_loudness(x, SR, chunk=777)
    assert a.integrated == pytest.approx(b.integrated, abs=0.05) and a.true_peak_db == pytest.approx(b.true_peak_db, abs=0.05)


# ---- mikrofon testi / auto-gain ---------------------------------------------------------------------------------------------------
def test_analyze_silence_and_too_short():
    assert analyze_level(np.zeros(SR, dtype=np.float32), SR).verdict == "silent"
    assert analyze_level(np.zeros(10, dtype=np.float32), SR).verdict == "silent"
    assert analyze_level(np.zeros(SR, dtype=np.float32), SR, current_gain_db=5).gain_db == 5      # öneri yok → değişmez


def test_analyze_quiet_speech_suggests_gain_to_reach_minus_18_dbfs():
    x = speechlike(5, amp=0.02, noise=0.0005)
    res = analyze_level(x, SR)
    assert res.verdict == "quiet" and res.level_dbfs < -30
    assert res.delta_db > 8 and res.speech_ratio > 0.2
    applied = x * db_to_lin(res.delta_db)
    again = analyze_level(applied, SR, current_gain_db=res.gain_db)
    assert again.level_dbfs == pytest.approx(dsp.TARGET_DBFS, abs=1.0) or again.limited_by_peak


def test_analyze_good_and_loud_levels():
    good = analyze_level(speechlike(5, amp=0.25), SR)
    assert good.verdict == "good" and -24 < good.level_dbfs < -12
    loud = analyze_level(speechlike(5, amp=0.9, noise=0.0), SR)
    assert loud.verdict in ("loud", "clipping") and loud.delta_db < 0


def test_analyze_clipping_verdict_and_peak_ceiling():
    x = np.clip(speechlike(4, amp=3.0), -1, 1)
    res = analyze_level(x, SR)
    assert res.verdict == "clipping"
    quiet_body_loud_peak = speechlike(4, amp=0.02, noise=0.0)
    quiet_body_loud_peak[SR] = 0.9                                             # tek yüksek tepe kazancı sınırlar
    lim = analyze_level(quiet_body_loud_peak, SR)
    assert lim.limited_by_peak and lim.peak_dbfs + lim.delta_db <= -2.9


def test_analyze_respects_current_gain_and_bounds():
    x = speechlike(4, amp=0.2)
    res = analyze_level(x, SR, current_gain_db=10.0)
    assert res.gain_db == pytest.approx(10.0 + res.delta_db, abs=1e-6)
    tiny = analyze_level(speechlike(4, amp=0.0006, noise=0.0), SR, max_gain_db=12)
    assert tiny.gain_db <= 12.0


def test_noise_floor_is_excluded_from_the_speech_level():
    voiced = speechlike(6, amp=0.1, noise=0.0)
    hissy = speechlike(6, amp=0.1, noise=0.004)
    assert analyze_level(hissy, SR).level_dbfs == pytest.approx(analyze_level(voiced, SR).level_dbfs, abs=1.5)
