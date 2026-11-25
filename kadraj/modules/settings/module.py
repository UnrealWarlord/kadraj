"""Ayarlar modülü girişi (``kadraj/modules/settings``).

Ayarlar ana ekranda kart değildir; üst çubuktaki dişliyle açılır (``kadraj.ui.shell``).
``register()`` ağır bağımlılık yüklemez: sayfa ve servis fabrikaları içe aktarımı tembel yapar.
"""
from __future__ import annotations

from ...core.contracts import Accent, ModuleSpec
from .strings import STRINGS


def register() -> ModuleSpec:
    def simple_page(ctx):
        from .page_simple import SettingsSimplePage
        return SettingsSimplePage(ctx)

    def legend_page(ctx):
        from .page_legend import SettingsLegendPage
        return SettingsLegendPage(ctx)

    def service(_ctx):
        from .service import SettingsService
        return SettingsService()

    return ModuleSpec(
        id="settings",
        title_key="mod.settings.title",
        order=90,
        accent=Accent.NEUTRAL,
        icon="gear",
        desc_key="mod.settings.desc",
        chip="",
        shortcut="",
        workspace="system",
        simple_page=simple_page,
        legend_page=legend_page,
        service_factory=service,
        requires=(),
        strings=STRINGS,
    )
