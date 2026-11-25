"""Tema: tokenlar plan §4 ile birebir, erişilebilirlik katmanları, QSS, yönetici, geçiş süreleri."""
from __future__ import annotations

import re

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from kadraj.core.contracts import Accent, UiMode
from kadraj.ui.theme import tokens as T
from kadraj.ui.theme.manager import REDUCED_MS, TRANSITION_MS, ThemeManager, current_tokens
from kadraj.ui.theme.qss import build_qss


def test_simple_tokens_match_plan_section_4():
    s = T.SIMPLE
    assert (s.bg, s.card, s.surface2, s.border, s.text, s.text2) == \
        ("#FBF4EA", "#FFFDF9", "#F6ECDD", "#EFE2D0", "#3B2F2A", "#6E5D52")
    assert s.accent(Accent.PEACH).base == "#FFB38A" and s.accent(Accent.PEACH).tone == "#FFE7D6"
    assert s.primary_deep == "#F08A5D"
    assert (s.accent(Accent.LAVENDER).base, s.accent(Accent.LAVENDER).tone, s.accent(Accent.LAVENDER).ink) == \
        ("#C9B8FF", "#EFE9FF", "#5B44B8")
    assert (s.accent(Accent.MINT).base, s.accent(Accent.MINT).tone, s.accent(Accent.MINT).ink) == \
        ("#9FE0C1", "#E1F6EC", "#2E5E48")
    assert (s.accent(Accent.BUTTER).base, s.accent(Accent.BUTTER).tone) == ("#FFD97A", "#FFF4D1")
    assert (s.accent(Accent.PINK).base, s.accent(Accent.PINK).tone) == ("#FFB3C4", "#FFE6EC")
    assert 20 <= s.radius_card <= 28 and s.pill and 48 <= s.btn_h <= 64 and s.font_ui == "Nunito"


def test_legend_tokens_match_plan_section_4():
    l = T.LEGEND
    assert (l.bg, l.card, l.surface2, l.border, l.text, l.text2) == \
        ("#0C0D10", "#15171C", "#1A1D23", "#262A31", "#E8E6E1", "#9BA1AB")
    assert l.primary == "#D4A24C" and l.info == "#3FD0E0" and l.good == "#46D18A" and l.bad == "#FF5A5F" \
        and l.warn == "#F5A04A"
    assert 2 <= l.radius_card <= 4 and not l.pill and 20 <= l.ctl_h <= 26 and l.ui_px == 12
    assert l.font_ui == "Inter" and l.font_mono == "JetBrains Mono" and l.border_w == 1


def test_large_text_layer_scales_by_1_25():
    base, big = T.resolve("simple"), T.resolve("simple", large_text=True)
    assert big.scale == 1.25 and big.large_text
    assert big.ui_px == round(base.ui_px * 1.25) and big.btn_h == round(base.btn_h * 1.25)
    assert big.px(100) == 125 and base.px(100) == 100


def test_high_contrast_layer():
    for mode in ("simple", "legend"):
        hc = T.resolve(mode, high_contrast=True)
        assert hc.high_contrast and hc.border_w == 2 and not hc.shadows
        bg = [int(hc.bg[i:i + 2], 16) for i in (1, 3, 5)]
        fg = [int(hc.text[i:i + 2], 16) for i in (1, 3, 5)]
        assert abs(sum(bg) - sum(fg)) > 600                              # siyah/beyaz zıtlığı
    both = T.resolve("legend", large_text=True, high_contrast=True)
    assert both.large_text and both.high_contrast


def test_resolve_unknown_mode_falls_back_to_simple():
    assert T.resolve("???").mode == "simple"


def test_every_accent_resolves_in_both_modes():
    for tok in (T.SIMPLE, T.LEGEND):
        for acc in Accent:
            a = tok.accent(acc)
            assert re.fullmatch(r"#[0-9A-F]{6}", a.base) and re.fullmatch(r"#[0-9A-F]{6}", a.tone)
        assert tok.accent("nonexistent") is tok.accent(Accent.NEUTRAL)


def test_mix_and_alpha():
    assert T.mix("#000000", "#FFFFFF", 0.5) == "#808080"
    assert T.alpha("#FF0000", 0.5).startswith("rgba(255, 0, 0")


def test_qss_uses_the_tokens(qapp):
    for tok in (T.SIMPLE, T.LEGEND, T.resolve("legend", high_contrast=True)):
        qss = build_qss(tok)
        assert tok.bg in qss and tok.text in qss and f"{tok.ui_px}px" in qss
    assert "QMenuBar" in build_qss(T.LEGEND) and "QDockWidget::title" in build_qss(T.LEGEND)


def test_manager_applies_mode_and_emits(qapp, qtbot):
    mgr = ThemeManager()
    mgr.set_animations(False)
    seen = []
    mgr.tokensChanged.connect(lambda tk: seen.append(tk.mode))
    mgr.apply("legend", animate=False)
    assert current_tokens().mode == "legend" and seen == ["legend"]
    assert T.LEGEND.bg in QApplication.instance().styleSheet()
    mgr.apply("simple", animate=False)
    assert current_tokens().mode == "simple" and mgr.mode is UiMode.SIMPLE


def test_transition_durations(qapp):
    mgr = ThemeManager()
    assert mgr.transition_ms == TRANSITION_MS == 480
    mgr.apply(reduced_motion=True, animate=False)
    assert mgr.transition_ms == REDUCED_MS == 120
    mgr.set_animations(False)
    assert mgr.transition_ms == 0


def test_crossfade_overlay_runs_and_cleans_up(qapp, qtbot):
    mgr = ThemeManager()
    mgr.apply("simple", animate=False)
    win = QLabel("x")
    win.resize(300, 200)
    win.show()
    qtbot.addWidget(win)
    mgr.apply("legend", animate=True, reduced_motion=True)               # 120 ms
    assert len(mgr._overlays) == 1
    qtbot.waitUntil(lambda: not mgr._overlays, timeout=2000)


def test_manager_syncs_from_settings(qapp, clean_home):
    from kadraj.core.settings import SettingsStore
    s = SettingsStore()
    s.set("ui_mode", "legend")
    s.set("a11y.large_text", True)
    mgr = ThemeManager(s)
    assert mgr.mode is UiMode.LEGEND and mgr.large_text and mgr.tokens.scale == 1.25


def test_reduced_motion_env(qapp, monkeypatch, clean_home):
    from kadraj.core.settings import SettingsStore
    monkeypatch.setenv("KADRAJ_REDUCED_MOTION", "1")
    assert ThemeManager(SettingsStore()).reduced_motion


def test_first_apply_installs_the_stylesheet_and_repeats_are_idempotent(qapp):
    """Gerileme: apply() ilk çağrıda QSS'i yüklemeli (yalnızca mod değişince değil); aynı tokenlarla tekrar yüklemez."""
    QApplication.instance().setStyleSheet("")
    mgr = ThemeManager()
    emitted = []
    mgr.tokensChanged.connect(emitted.append)
    mgr.apply("simple", animate=False)
    assert QApplication.instance().styleSheet() and T.SIMPLE.bg in QApplication.instance().styleSheet()
    assert len(emitted) == 1
    mgr.apply("simple", animate=False)
    assert len(emitted) == 1                                             # değişmedi → yeniden kurulmadı


def test_create_app_leaves_the_application_styled(qapp, clean_home):
    from kadraj.core.settings import SettingsStore
    from kadraj.ui.shell.app import create_app
    QApplication.instance().setStyleSheet("")
    _app, context, theme = create_app(["kadraj"], settings=SettingsStore())
    assert QApplication.instance().styleSheet()
    lab = QLabel("x")
    lab.setProperty("role", "title")
    lab.ensurePolished()
    assert lab.font().family() == "Nunito"                               # role=title QSS'i uygulandı
    context.shutdown()
