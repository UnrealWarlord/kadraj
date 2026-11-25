"""``DockPanel`` — Legend'in profesyonel panelleri için temalı ``QDockWidget`` (BÜYÜK HARFLİ başlık, ince kenarlık, taşınabilir)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QDockWidget, QHBoxLayout, QLabel, QWidget

from ..theme.manager import ThemedMixin


class _Title(ThemedMixin, QWidget):
    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(10, 4, 8, 4)
        self._lay.setSpacing(6)
        self.label = QLabel(text.upper())
        self.label.setProperty("role", "caps")
        self._lay.addWidget(self.label)
        self._lay.addStretch(1)
        self._bind_theme()

    def add_widget(self, w: QWidget) -> None:
        self._lay.addWidget(w)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setPen(self.tk.border)
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


class DockPanel(QDockWidget):
    """``DockPanel("Zaman çizelgesi", content)``; ``objectName`` zorunludur (``saveState`` için)."""

    def __init__(self, object_name: str, title: str, content: Optional[QWidget] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(title, parent)
        self.setObjectName(object_name)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable |
                         QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self._title = _Title(title, self)
        self.setTitleBarWidget(self._title)
        if content is not None:
            self.setWidget(content)

    def set_title(self, text: str) -> None:
        self.setWindowTitle(text)
        self._title.label.setText(text.upper())

    def add_title_widget(self, w: QWidget) -> None:
        """Başlık çubuğunun sağına küçük bir denetim (sekme, düğme) ekler."""
        self._title.add_widget(w)
