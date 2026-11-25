"""Legend Hızlı Başlat: etiket ile kısayol hiçbir dilde ve hiçbir kartta üst üste binmez (offscreen ölçüm)."""
from __future__ import annotations

import pytest

from kadraj.ui.shell.legend import QuickStartPanel


@pytest.mark.parametrize("lang", ["tr", "en"])
def test_legend_quickstart_label_never_overlaps_shortcut(ctx, qtbot, lang):
    ctx.set_language(lang)
    ctx.set_ui_mode("legend")
    panel = QuickStartPanel(ctx)
    qtbot.addWidget(panel)
    checked = 0
    for card in panel.cards:
        card.resize(card.minimumWidth(), card.height())   # kenar çubuğunun verebileceği en dar genişlik
        shown, label_right, shortcut_left = card.legend_label_edges()
        if shortcut_left is None:
            continue
        checked += 1
        assert label_right <= shortcut_left, f"{lang}: {card.title!r} etiketi kısayolla çakışıyor"
        assert shown == card.title, f"{lang}: {card.title!r} etiketi kırpıldı (genişlik yetersiz)"
    assert checked >= 5, "kısayollu kart sayısı beklenenden az"
