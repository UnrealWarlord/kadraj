"""Sayfa zemini: kök widget ve tablo/liste yüzeyleri widget düzeyinde tokenlardan boyanır.

Neden: uygulama QSS'indeki ``QAbstractScrollArea { background: transparent; }`` kuralı, genel tablo/liste
kuralından sonra geldiği için ``QTableWidget`` ve ``QListWidget`` zeminini şeffaf bırakır. Widget düzeyindeki
stil sayfası uygulama stil sayfasının üzerinde uygulanır; bu yüzden zemin yalnızca bu sayfanın kendi
tokenlarından (Basit: pastel, Legend: koyu) gelir ve tema değiştiğinde yeniden boyanır.
"""
from __future__ import annotations


def page_surface_qss(tk, object_name: str) -> str:
    """``object_name`` kök widget'ın nesne adıdır (``setObjectName`` ile aynı olmalı)."""
    sel = f"#{object_name}"
    return f"""
{sel} {{ background: {tk.bg}; }}
{sel} QScrollArea, {sel} QScrollArea > QWidget, {sel} QScrollArea > QWidget > QWidget {{
    background: {tk.bg}; border: none;
}}
{sel} QTableWidget, {sel} QListWidget {{
    background: {tk.card}; alternate-background-color: {tk.surface2}; color: {tk.text};
    border: 1px solid {tk.border}; border-radius: {tk.radius_ctl}px;
    selection-background-color: {tk.primary_tone}; selection-color: {tk.text};
}}
{sel} QHeaderView::section {{
    background: {tk.surface2}; color: {tk.text2}; border: none; padding: 4px 8px; font-weight: 700;
}}
"""
