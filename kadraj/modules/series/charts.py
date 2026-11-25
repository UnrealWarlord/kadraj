"""Maliyet / token analitiği grafikleri (8a ``analytics.ChartData``): günlük yığılmış sütunlar + bütçe çizgisi, sağlayıcı payı, token toplamı."""
from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ...ai_engine import analytics
from ...core import i18n
from ...ui import fonts
from ...ui.theme.manager import ThemedMixin

PALETTE = ("#D4A24C", "#A68BFF", "#3FD0E0", "#FF7A93", "#46D18A", "#F5A04A", "#7AA2F7", "#9BA1AB")


class CostChart(ThemedMixin, QWidget):
    """Günlük maliyet: sağlayıcıya göre yığılmış sütunlar; bütçe verilmişse yatay çizgi (aylık bütçe / 30)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.data: Optional[analytics.ChartData] = None
        self.setMinimumSize(300, 140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._bind_theme()

    def set_data(self, data: analytics.ChartData) -> None:
        self.data = data
        self.update()

    def color_of(self, provider: str) -> QColor:
        names = self.data.providers if self.data else []
        return QColor(PALETTE[names.index(provider) % len(PALETTE)] if provider in names else PALETTE[-1])

    def paintEvent(self, _e) -> None:
        tk = self.tk
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(tk.surface2))
        d = self.data
        r = QRectF(self.rect()).adjusted(36, 10, -10, -20)
        p.setPen(QColor(tk.text2))
        p.setFont(fonts.qfont(tk.font_mono, tk.ui_px * 0.75))
        if d is None or not d.days or max(d.totals or [0]) <= 0:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, i18n.t("series.charts.empty"))
            return
        top = max(max(d.totals), 0.0001) * 1.15
        for frac in (0, 0.5, 1):
            y = r.bottom() - r.height() * frac
            p.setPen(QPen(QColor(tk.border), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(tk.text2))
            p.drawText(QRectF(0, y - 7, 34, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"${top * frac:.2f}")
        n = len(d.days)
        bw = r.width() / n
        for i in range(n):
            y = r.bottom()
            for prov in d.providers:
                v = d.series[prov][i]
                if v <= 0:
                    continue
                h = r.height() * v / top
                p.fillRect(QRectF(r.left() + i * bw + bw * 0.15, y - h, bw * 0.7, h), self.color_of(prov))
                y -= h
        bud = d.budget or {}
        if bud.get("budget"):
            by = r.bottom() - r.height() * (float(bud["budget"]) / 30.0) / top
            if by > r.top():
                p.setPen(QPen(QColor(tk.warn), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(r.left(), by), QPointF(r.right(), by))
        p.setPen(QColor(tk.text2))
        p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), 14), Qt.AlignmentFlag.AlignLeft, d.days[0][5:])
        p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), 14), Qt.AlignmentFlag.AlignRight, d.days[-1][5:])


class AnalyticsPanel(QWidget):
    """Grafik + sağlayıcı tablosu + token/bütçe özeti."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        self.chart = CostChart()
        self.summary = QLabel("")
        self.summary.setProperty("role", "mono")
        left.addWidget(self.chart, 1)
        left.addWidget(self.summary)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([i18n.t("series.charts.provider"), i18n.t("series.charts.calls"), i18n.t("series.charts.tokens"),
                                              i18n.t("series.charts.cost"), i18n.t("series.charts.share")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addLayout(left, 3)
        lay.addWidget(self.table, 2)

    def set_rows(self, rows: List[dict], data: analytics.ChartData) -> None:
        self.chart.set_data(data)
        total = sum(float(r.get("cost_usd") or 0) for r in rows)
        tok = data.tokens or {}
        bud = data.budget or {}
        extra = f" · {bud['percent']:.0f}% / ${bud['budget']:.0f}" if bud.get("budget") else ""
        self.summary.setText(i18n.t("series.charts.summary", cost=f"{total:.2f}", tin=tok.get("tokens_in", 0), tout=tok.get("tokens_out", 0)) + extra)
        table = analytics.provider_breakdown(rows)
        self.table.setRowCount(len(table))
        for i, t in enumerate(table):
            for j, v in enumerate((t.provider, str(t.calls), f"{t.tokens_in + t.tokens_out:,}", f"${t.cost_usd:.3f}", f"{t.share * 100:.0f}%")):
                self.table.setItem(i, j, QTableWidgetItem(v))
