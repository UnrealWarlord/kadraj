"""Token → QSS dönüşümü.

Genel kontroller (giriş alanları, sekmeler, tablolar, kaydırma çubukları, menüler,
dock başlıkları) buradan biçimlenir. Özel çizen bileşenler (``Lockup``, ``ModeSwitch``,
``SquishyButton``…) kendi ``paintEvent``'lerinde aynı tokenları kullanır.

Kabuk widget'ları ``role`` dinamik özelliğiyle (``setProperty("role", "card")``) hedeflenir;
böylece ad çakışması ve sınıf bağımlılığı olmaz.
"""
from __future__ import annotations

from .. import fonts
from .tokens import Tokens, alpha, mix


def _ff(token_family: str, generic: str = "sans-serif") -> str:
    real = fonts.resolve(token_family)
    return f'"{real}", "{token_family}", {generic}'


def build_qss(tk: Tokens) -> str:
    ui = _ff(tk.font_ui)
    mono = _ff(tk.font_mono, "monospace")
    bw = tk.border_w
    rc, rt = tk.radius_card, tk.radius_ctl
    fs = tk.ui_px
    small = max(9, round(fs * 0.85))
    hover = tk.surface2
    sel = tk.primary_tone
    focus = tk.primary if not tk.high_contrast else tk.text
    pad_y = max(2, round(tk.ctl_h * 0.18))
    line = f"{bw}px solid {tk.border}"
    scroll_w = 10 if not tk.is_legend else 8

    return f"""
* {{ outline: 0; }}
QWidget {{
    color: {tk.text};
    font-family: {ui};
    font-size: {fs}px;
    font-weight: {tk.ui_weight};
}}
QMainWindow, QDialog, QStackedWidget, QStackedWidget > QWidget, #Root {{ background: {tk.bg}; }}
QLabel {{ background: transparent; border: none; }}
QLabel[role="muted"], QLabel[role="hint"] {{ color: {tk.text2}; }}
QLabel[role="title"] {{
    font-family: {_ff(tk.font_display)}; font-weight: {tk.display_weight};
    font-size: {round(fs * (2.0 if not tk.is_legend else 1.5))}px;
}}
QLabel[role="h2"] {{
    font-family: {_ff(tk.font_display)}; font-weight: {tk.display_weight};
    font-size: {round(fs * (1.35 if not tk.is_legend else 1.15))}px;
}}
QLabel[role="caps"] {{
    color: {tk.text2}; font-size: {small}px; font-weight: 700; letter-spacing: 1px;
}}
QLabel[role="mono"] {{ font-family: {mono}; }}

/* --- yüzeyler --- */
QFrame[role="card"] {{
    background: {tk.card}; border: {line}; border-radius: {rc}px;
}}
QFrame[role="card-tone"] {{ border: none; border-radius: {rc}px; }}
QFrame[role="surface2"] {{ background: {tk.surface2}; border: none; border-radius: {rt}px; }}
QFrame[role="panel"] {{ background: {tk.card}; border: {line}; border-radius: {tk.radius_card}px; }}
QFrame[role="topbar"] {{
    background: {tk.card}; border: none; border-bottom: {line}; border-radius: 0px;
}}
QFrame[role="statusbar"] {{
    background: {tk.surface2 if tk.is_legend else tk.card}; border: none; border-top: {line}; border-radius: 0px;
}}
QFrame[role="divider"] {{ background: {tk.border}; border: none; max-height: {bw}px; min-height: {bw}px; }}
QFrame[role="vdivider"] {{ background: {tk.border}; border: none; max-width: {bw}px; min-width: {bw}px; }}
QFrame[role="pill"] {{
    background: {tk.surface2}; border: none; border-radius: {max(6, tk.ctl_h // 2 - 4)}px;
}}

/* --- giriş alanları --- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {tk.card if not tk.is_legend else tk.bg};
    border: {line}; border-radius: {rt}px;
    padding: {pad_y}px {round(tk.ctl_h * 0.3)}px; min-height: {tk.ctl_h - 2 * pad_y - 2 * bw}px;
    selection-background-color: {tk.primary}; selection-color: {tk.on_primary};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus {{ border: {max(bw, 2)}px solid {focus}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {tk.text2}; background: {tk.surface2}; }}
QComboBox::drop-down {{ border: none; width: {round(tk.ctl_h * 0.7)}px; }}
QComboBox QAbstractItemView {{
    background: {tk.card}; border: {line}; selection-background-color: {sel}; selection-color: {tk.text};
    outline: 0;
}}

/* --- düğmeler (SquishyButton dışındaki sade QPushButton) --- */
QPushButton {{
    background: {tk.card}; border: {line}; border-radius: {rt if not tk.pill else tk.ctl_h // 2}px;
    padding: 0 {round(tk.ctl_h * 0.5)}px; min-height: {tk.ctl_h}px;
}}
QPushButton:hover {{ background: {hover}; }}
QPushButton:pressed {{ background: {tk.border}; }}
QPushButton:focus {{ border: {max(bw, 2)}px solid {focus}; }}
QPushButton:disabled {{ color: {tk.text2}; background: {tk.surface2}; }}
QToolButton {{
    background: transparent; border: none; border-radius: {rt}px; padding: {pad_y}px;
}}
QToolButton:hover {{ background: {hover}; }}
QToolButton:checked {{ background: {sel}; }}
QToolButton:focus {{ border: {max(bw, 2)}px solid {focus}; }}

QCheckBox, QRadioButton {{ spacing: {round(8 * tk.scale)}px; background: transparent; }}
QCheckBox:focus, QRadioButton:focus {{ color: {focus}; }}

/* --- ilerleme --- */
QProgressBar {{
    background: {tk.surface2}; border: none; border-radius: {max(2, rt // 2)}px;
    min-height: {round(6 * tk.scale)}px; max-height: {round(6 * tk.scale)}px; text-align: center;
}}
QProgressBar::chunk {{ background: {tk.primary}; border-radius: {max(2, rt // 2)}px; }}

/* --- sekmeler --- */
QTabWidget::pane {{ border: {line}; border-radius: {rt}px; background: {tk.card}; top: -{bw}px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {tk.text2}; padding: {pad_y}px {round(tk.ctl_h * 0.5)}px;
    min-height: {tk.ctl_h - 2 * pad_y}px; border: none;
    border-bottom: {max(2, bw)}px solid transparent; margin-right: 2px;
}}
QTabBar::tab:selected {{ color: {tk.text}; border-bottom: {max(2, bw)}px solid {tk.primary}; }}
QTabBar::tab:hover:!selected {{ color: {tk.text}; background: {hover}; }}

/* --- listeler, tablolar --- */
QListView, QTreeView, QTableView, QListWidget, QTreeWidget, QTableWidget {{
    background: {tk.card}; alternate-background-color: {tk.surface2}; border: {line};
    border-radius: {rt}px; gridline-color: {tk.border};
    selection-background-color: {sel}; selection-color: {tk.text};
}}
QListView::item, QTreeView::item, QTableView::item {{ padding: {max(2, pad_y // 2)}px; }}
QHeaderView::section {{
    background: {tk.surface2}; color: {tk.text2}; border: none; border-bottom: {line};
    padding: {pad_y}px {round(tk.ctl_h * 0.3)}px; font-size: {small}px; font-weight: 700; text-align: left;
}}

/* --- menü çubuğu --- */
QMenuBar {{
    background: {tk.surface2 if tk.is_legend else tk.card}; color: {tk.text}; border: none;
    border-bottom: {line}; padding: 2px 6px;
}}
QMenuBar::item {{ padding: 3px 10px; background: transparent; border-radius: {max(2, rt - 2)}px; }}
QMenuBar::item:selected {{ background: {sel}; }}
QToolButton#ProjectButton::menu-indicator {{ image: none; width: 0px; }}

/* --- kaydırma çubukları --- */
QScrollBar:vertical {{ background: transparent; width: {scroll_w}px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: {scroll_w}px; margin: 0; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {mix(tk.border, tk.text2, 0.35)}; border-radius: {scroll_w // 2}px;
    min-height: 24px; min-width: 24px;
}}
QScrollBar::handle:hover {{ background: {tk.text2}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QAbstractScrollArea {{ background: transparent; }}

/* --- menü, ipucu --- */
QMenu {{
    background: {tk.card}; border: {line}; border-radius: {rt}px; padding: 4px;
}}
QMenu::item {{ padding: {pad_y}px {round(tk.ctl_h * 0.6)}px; border-radius: {max(2, rt - 4)}px; }}
QMenu::item:selected {{ background: {sel}; color: {tk.text}; }}
QMenu::separator {{ height: {bw}px; background: {tk.border}; margin: 4px 6px; }}
QToolTip {{
    background: {tk.text}; color: {tk.bg}; border: none; padding: 6px 10px;
    border-radius: {rt}px; font-weight: 600;
}}

/* --- dock paneller (Legend) --- */
QMainWindow::separator {{ background: {tk.border}; width: {bw}px; height: {bw}px; }}
QMainWindow::separator:hover {{ background: {tk.primary}; }}
QDockWidget {{ color: {tk.text}; titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{
    background: {tk.surface2}; padding: {max(3, pad_y)}px 8px; border: {line};
    border-bottom: none; text-align: left; font-size: {small}px; font-weight: 700;
}}
QSplitter::handle {{ background: {tk.border}; }}
QSplitter::handle:horizontal {{ width: {bw}px; }}
QSplitter::handle:vertical {{ height: {bw}px; }}

/* --- sihirbaz / seçilebilir kartlar --- */
QPushButton[role="choice"] {{
    text-align: left; background: {tk.card}; border: {max(2, bw)}px solid {tk.border};
    border-radius: {rc}px; padding: {round(tk.ctl_h * 0.35)}px {round(tk.ctl_h * 0.45)}px;
    min-height: {round(tk.ctl_h * 1.5)}px;
}}
QPushButton[role="choice"]:hover {{ background: {hover}; }}
QPushButton[role="choice"]:checked {{ background: {sel}; border-color: {tk.primary}; }}
QPushButton[role="choice"]:focus {{ border-color: {focus}; }}
QPushButton[role="link"] {{
    background: transparent; border: none; color: {tk.text2}; text-decoration: underline;
    padding: 0 4px; min-height: 0;
}}
QPushButton[role="link"]:hover {{ color: {tk.text}; background: transparent; }}
QPushButton[role="topbtn"] {{
    background: transparent; border: {line}; border-radius: {rt if not tk.pill else tk.ctl_h // 2}px;
    padding: 0 {round(tk.ctl_h * 0.4)}px; min-height: {round(tk.ctl_h * 0.8)}px; font-size: {small}px;
}}
QPushButton[role="topbtn"]:hover {{ background: {hover}; }}
QPushButton[role="topbtn"]:disabled {{ color: {mix(tk.text2, tk.bg, 0.5)}; border-color: {tk.border}; }}
QPushButton[role="gear"] {{ min-height: 0; padding: 0; border-radius: {tk.ctl_h // 2}px; }}
QWidget#SplashRoot {{ background: {tk.bg}; }}
QWidget[role="toast"] {{
    background: {tk.text}; color: {tk.bg}; border-radius: {rt}px;
}}
QLabel[role="toast-text"] {{ color: {tk.bg}; font-weight: 700; }}
QLabel[role="chip"] {{
    background: {alpha(tk.text, 0.08)}; border-radius: {max(4, tk.ctl_h // 3)}px;
    padding: 1px {round(8 * tk.scale)}px; font-size: {small}px; font-weight: 700;
}}
"""
