"""Medya İndirici modül girişi (``kadraj/modules/downloader``).

``register()`` ağır bağımlılık yüklemez: sayfa ve servis fabrikaları içe aktarımı ilk
kullanımda yapar (bkz. ``contracts.DISCOVERY_RULES``). yt-dlp yalnızca servis içinde yüklenir.
"""
from __future__ import annotations

from ...core.contracts import Accent, ModuleSpec
from .strings import STRINGS


def register() -> ModuleSpec:
    def simple_page(ctx):
        from .page_simple import DownloaderSimplePage
        return DownloaderSimplePage(ctx)

    def legend_page(ctx):
        from .page_legend import DownloaderLegendPage
        return DownloaderLegendPage(ctx)

    def service(_ctx):
        from .service import DownloaderService
        return DownloaderService()

    return ModuleSpec(
        id="downloader",
        title_key="mod.downloader.title",
        order=10,
        accent=Accent.PEACH,
        icon="download",
        desc_key="mod.downloader.desc",
        chip="",
        shortcut="Ctrl+1",
        workspace="capture",
        simple_page=simple_page,
        legend_page=legend_page,
        service_factory=service,
        requires=("ffmpeg",),
        strings=STRINGS,
    )
