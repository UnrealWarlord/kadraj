"""Tema yöneticisi — mod geçişi, Büyük Yazı / Yüksek Kontrast, çapraz geçiş animasyonu.

* Mod değişince **önce** pencerenin anlık görüntüsü alınıp üstüne bindirilir, **altta** yeni QSS
  uygulanır, sonra bindirme saydamlaşarak kaybolur (çapraz geçiş). Süre: **480 ms**; "Hareketi
  Azalt" açıksa **120 ms**. Animasyon yalnızca görseldir; durum etkilenmez.
* ``tokens`` her zaman güncel nihai ``Tokens``'tır; özel çizen widget'lar ``tokensChanged``'a
  bağlanıp ``update()`` çağırır (``ThemedMixin``).
"""
from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtCore import (QEasingCurve, QObject, QPropertyAnimation, Qt, Signal)
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QLabel, QWidget)

from ...core.contracts import UiMode
from .. import fonts
from . import tokens as _tokens
from .qss import build_qss

TRANSITION_MS = 480
REDUCED_MS = 120

_active: Optional["ThemeManager"] = None


def active_manager() -> Optional["ThemeManager"]:
    return _active


def current_tokens() -> _tokens.Tokens:
    """Etkin tokenlar; yönetici yoksa Basit varsayılanı."""
    return _active.tokens if _active is not None else _tokens.SIMPLE


class ThemeManager(QObject):
    tokensChanged = Signal(object)        # yeni Tokens
    modeChanged = Signal(str)

    def __init__(self, settings=None, parent: Optional[QObject] = None) -> None:
        global _active
        super().__init__(parent)
        self._settings = settings
        self._mode = UiMode.SIMPLE
        self._large = False
        self._contrast = False
        self._reduced = False
        self._animate = True
        self._tokens = _tokens.resolve(self._mode)
        self._installed: Optional[_tokens.Tokens] = None      # uygulamaya gerçekten yüklenmiş tokenlar
        self._overlays: List[QWidget] = []
        if settings is not None:
            self.sync_from_settings()
        _active = self

    # -- özellikler -------------------------------------------------------
    @property
    def tokens(self) -> _tokens.Tokens:
        return self._tokens

    @property
    def mode(self) -> UiMode:
        return self._mode

    @property
    def large_text(self) -> bool:
        return self._large

    @property
    def high_contrast(self) -> bool:
        return self._contrast

    @property
    def reduced_motion(self) -> bool:
        return self._reduced

    @property
    def transition_ms(self) -> int:
        """Çapraz geçiş süresi: normal 480 ms, Hareketi Azalt 120 ms, animasyon kapalıysa 0."""
        if not self._animate:
            return 0
        return REDUCED_MS if self._reduced else TRANSITION_MS

    def set_animations(self, enabled: bool) -> None:
        """Yalnızca testler/ekran görüntüsü için: ``False`` ise geçiş anında olur."""
        self._animate = bool(enabled)

    # -- ayar senkronu ----------------------------------------------------
    def sync_from_settings(self) -> None:
        s = self._settings
        self._mode = UiMode.coerce(s.get("ui_mode"))
        self._large = bool(s.get("a11y.large_text", False))
        self._contrast = bool(s.get("a11y.high_contrast", False))
        self._reduced = bool(s.get("a11y.reduced_motion", False)) or \
            os.environ.get("KADRAJ_REDUCED_MOTION") == "1"
        self._tokens = _tokens.resolve(self._mode, self._large, self._contrast)

    # -- uygulama ---------------------------------------------------------
    def apply(self, mode: "UiMode | str | None" = None, *, animate: bool = True,
              large_text: Optional[bool] = None, high_contrast: Optional[bool] = None,
              reduced_motion: Optional[bool] = None) -> None:
        """Modu/katmanları günceller, QSS'i uygular, ``tokensChanged`` yayınlar."""
        if mode is not None:
            self._mode = UiMode.coerce(mode)
        if large_text is not None:
            self._large = bool(large_text)
        if high_contrast is not None:
            self._contrast = bool(high_contrast)
        if reduced_motion is not None:
            self._reduced = bool(reduced_motion)
        new = _tokens.resolve(self._mode, self._large, self._contrast)
        if new == self._installed:                   # zaten yüklü: QSS'i yeniden kurma (ilk çağrıda yüklenir)
            return
        changed_mode = new.mode != self._tokens.mode
        snapshots = self._snapshot_windows() if (animate and changed_mode and self.transition_ms > 0) else []
        self._tokens = new
        self._install(new)
        self.tokensChanged.emit(new)
        if changed_mode:
            self.modeChanged.emit(new.mode)
        for win, pix in snapshots:
            self._crossfade(win, pix)

    def _install(self, tk: _tokens.Tokens) -> None:
        app = QApplication.instance()
        if app is None:
            return
        base = QFont(fonts.resolve(tk.font_ui))
        base.setPixelSize(tk.ui_px)
        base.setWeight(QFont.Weight(tk.ui_weight))
        qss = build_qss(tk)
        # Aynı yazı tipi/QSS zaten yüklüyse dokunma: setStyleSheet/setFont açık TÜM widget'ları yeniden boyar (pahalı);
        # örn. aynı süreçte art arda kurulan bağlamlar (testler) gereksiz yere yavaşlamasın.
        if app.font() != base:
            app.setFont(base)
        if app.styleSheet() != qss:
            app.setStyleSheet(qss)
        self._installed = tk

    # -- çapraz geçiş -----------------------------------------------------
    @staticmethod
    def _snapshot_windows() -> list:
        app = QApplication.instance()
        out = []
        if app is None:
            return out
        for win in app.topLevelWidgets():
            if win.isVisible() and win.width() > 40 and win.height() > 40 and win.isWindow() \
                    and not win.property("no_crossfade"):
                try:
                    out.append((win, win.grab()))
                except Exception:
                    continue
        return out

    def _crossfade(self, win: QWidget, pix: QPixmap) -> None:
        overlay = QLabel(win)
        overlay.setPixmap(pix)
        overlay.setGeometry(0, 0, win.width(), win.height())
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay.setProperty("no_crossfade", True)
        overlay.show()
        overlay.raise_()
        effect = QGraphicsOpacityEffect(overlay)
        effect.setOpacity(1.0)
        overlay.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", overlay)
        anim.setDuration(self.transition_ms)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._overlays.append(overlay)

        def _done() -> None:
            if overlay in self._overlays:
                self._overlays.remove(overlay)
            overlay.hide()
            overlay.deleteLater()

        anim.finished.connect(_done)
        anim.start()

    def finish_transitions(self) -> None:
        """Süren çapraz geçişleri hemen bitirir (testler / pencere kapanışı)."""
        for overlay in list(self._overlays):
            overlay.hide()
            overlay.deleteLater()
        self._overlays.clear()


class ThemedMixin:
    """Özel çizen widget'lar için: tema değişince ``_on_theme`` ile yeniden boyar.

    Kullanım: ``class X(ThemedMixin, QWidget)`` ve ``__init__`` sonunda ``self._bind_theme()``.
    ``self.tk`` her zaman güncel tokenlardır.
    """

    @property
    def tk(self) -> _tokens.Tokens:
        return current_tokens()

    def _bind_theme(self) -> None:
        mgr = active_manager()
        if mgr is not None:
            mgr.tokensChanged.connect(self._on_theme_changed)      # type: ignore[attr-defined]

    def _on_theme_changed(self, _tokens_obj: object) -> None:
        self._on_theme()
        self.update()                                              # type: ignore[attr-defined]

    def _on_theme(self) -> None:
        """Alt sınıflar boyut/yazı tipi gibi tema bağımlı durumu burada yeniler."""
