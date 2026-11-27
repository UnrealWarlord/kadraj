"""Uygulama akışı — splash → onay → (ilk açılışta) sihirbaz → ana pencere (iskeletin ``run`` akışının karşılığı).

``create_app`` Qt uygulamasını, yazı tiplerini, ayarları, bağlamı ve tema yöneticisini kurar (ekransız testlerde
de kullanılır). ``Launcher`` akışı olay döngüsüyle zincirler; ``run`` komut satırını işler.
Ağ çağrısı **yoktur**; internet olmadan da aynı akış çalışır.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from ... import consent, crashlog, device, paths
from ...core import updater
from ...core.context import AppContext
from ...core.contracts import UiMode
from ...core.settings import SettingsStore, apply_wizard_answers
from .. import fonts
from ..app_icon import app_icon
from ..scaling import configure_high_dpi, fit_window
from ..theme.manager import ThemeManager
from .dialogs import ensure_consent
from .main_window import MainWindow
from .splash import SplashScreen
from .wizard import WizardDialog


def create_app(argv: Optional[List[str]] = None, *, settings: Optional[SettingsStore] = None,
               discover: bool = True) -> Tuple[QApplication, AppContext, ThemeManager]:
    """QApplication (varsa mevcut olanı kullanır) + yazı tipleri + bağlam + tema. Pencere açmaz."""
    app = QApplication.instance()
    if app is None:
        configure_high_dpi()
        app = QApplication(argv if argv is not None else sys.argv)
        app.setApplicationName("kadraj")
        app.setOrganizationName("insibag")
        QGuiApplication.setDesktopFileName("kadraj")
        if device.is_android():
            # HOME → uygulamaya ayrılmış dış depolama (Documents); Path.home() kullanan her yer oraya yazar.
            documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
            home = paths.prepare_android_environment(documents)
            if home is not None:
                crashlog.install(home)          # yakalanmamış hata ve çökme → Belgeler/KADRAJ-hata.txt
            app.setStyle("Fusion")              # Android stili eklentisi paketlenmez; masaüstü Linux'takiyle aynı taban
    app.setWindowIcon(app_icon())
    fonts.load_fonts()
    ctx = AppContext(settings=settings, discover=discover)
    theme = ThemeManager(ctx.settings)
    ctx.theme = theme
    theme.apply(ctx.ui_mode, animate=False)
    return app, ctx, theme            # type: ignore[return-value]


def _note(text: str) -> None:
    """Android: açılış aşaması logcat'e yazılır (``crashlog.note``); masaüstünde sessiz."""
    if device.is_android():
        crashlog.note(text)


class Launcher:
    """splash → onay → sihirbaz → ana pencere zinciri. Her adım testlerde atlanabilir/sahtelenebilir."""

    def __init__(self, app: QApplication, ctx: AppContext, theme: ThemeManager, *, splash: bool = True,
                 splash_ms: Optional[int] = None, ask_consent: Optional[Callable[[], bool]] = None,
                 wizard: bool = True, updates: bool = False) -> None:
        self.app, self.ctx, self.theme = app, ctx, theme
        self.updates = updates          # açılışta bileşen güncelleme; testlerde ve --selftest'te hep kapalı
        self.use_splash = splash and bool(ctx.settings.get("splash", True))
        self.splash_ms = splash_ms
        self.ask_consent = ask_consent
        self.use_wizard = wizard
        self.window: Optional[MainWindow] = None
        self._splash: Optional[SplashScreen] = None

    def start(self) -> None:
        if device.is_android():
            width = device.screen_smallest_dp()
            _note(f"açılış: {device.form_factor()}" + (f", en küçük genişlik {width:.0f} dp" if width else ""))
        if self.use_splash:
            self._splash = SplashScreen(self.splash_ms, updates=self.updates)
            self._splash.finished.connect(self._after_splash)
            self._splash.start()
        else:
            self._after_splash()

    def _after_splash(self) -> None:
        if self._splash is not None:
            self._splash.deleteLater()
            self._splash = None
        if device.is_android() and not consent.is_accepted():
            _note("onay soruluyor")
        if not ensure_consent(None, self.ask_consent):
            self.app.quit()
            return
        if self.use_wizard and not self.ctx.settings.get("wizard.done"):
            _note("sihirbaz açıldı")
            dlg = WizardDialog()
            dlg.finishedWithAnswers.connect(self._apply_wizard)
            if dlg.exec() != WizardDialog.DialogCode.Accepted:
                # sihirbazı atlayan kullanıcı da uygulamayı kullanabilir; varsayılanlarla devam
                apply_wizard_answers(self.ctx.settings, dlg.answers())
        self.show_main()

    def _apply_wizard(self, answers) -> None:
        root = apply_wizard_answers(self.ctx.settings, answers)
        try:                                   # Adım 2'nin klasör üreticisi varsa kök klasörü hazırla
            from ...storage import folders  # type: ignore[attr-defined]
            prepare = getattr(folders, "ensure_root", None)
            if callable(prepare):
                prepare(root)
        except ImportError:
            pass
        except Exception as exc:
            self.ctx.report_error(exc, {"source": "wizard", "path": str(root)})
        self._wizard_api = answers.api

    def show_main(self) -> MainWindow:
        self.window = MainWindow(self.ctx, self.theme)
        if device.is_mobile():                 # telefon/tablet: pencere ekranın tamamı (çalışma alanı)
            self.window.showMaximized()
        else:
            fit_window(self.window, 1320, 860, min_width=920, min_height=620)
            self.window.show()
        if getattr(self, "_wizard_api", "") == "how":
            self.ctx.bus.emit("ui.navigate", {"module": "settings"})
        _note(f"ana pencere açıldı ({self.ctx.ui_mode.value})")
        return self.window


def run(argv: Optional[List[str]] = None, *, splash: bool = True, mode: Optional[str] = None) -> int:
    app, ctx, theme = create_app(argv)
    if mode:
        ctx.set_ui_mode(UiMode.coerce(mode))
        theme.apply(ctx.ui_mode, animate=False)
    launcher = Launcher(app, ctx, theme, splash=splash,
                        updates=splash and updater.auto_update_enabled(ctx.settings))
    launcher.start()
    code = app.exec()
    ctx.shutdown()
    return code
