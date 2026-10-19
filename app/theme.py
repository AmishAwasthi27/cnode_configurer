"""Application theming / QSS generation.

Design language:
- The whole UI uses a single navy accent; the Mode chip is the only element
  whose colour switches on mode change.
- The sidebar and top bar share the accent for a continuous coloured strip.
- **Layouts / containers are square** (no border radius). **Interactive widgets
  and tag-like elements** (buttons, inputs, chips, list items) stay rounded.
"""
from __future__ import annotations

import os

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from app.constants import ACCENT_MAINTENANCE, ACCENT_PRODUCTION


def _lighten(hex_color: str, factor: float) -> str:
    c = QColor(hex_color)
    h, s, v, a = c.getHsvF()
    v = min(1.0, v + (1.0 - v) * factor)
    c.setHsvF(h, s, v, a)
    return c.name()


def _darken(hex_color: str, factor: float) -> str:
    c = QColor(hex_color)
    h, s, v, a = c.getHsvF()
    v = max(0.0, v * (1 - factor))
    c.setHsvF(h, s, v, a)
    return c.name()


def build_qss() -> str:
    accent = ACCENT_PRODUCTION
    accent_hover = _lighten(accent, 0.15)
    accent_pressed = _darken(accent, 0.15)
    maintenance = ACCENT_MAINTENANCE
    maintenance_fg = "#1F2937"

    bg = "#F5F6FA"
    surface = "#FFFFFF"
    surface_alt = "#EEF1F7"
    border = "#D7DCE6"
    text = "#1F2937"
    muted = "#6B7280"

    return f"""
    * {{
        font-family: 'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif;
        font-size: 10pt;
        color: {text};
    }}
    QMainWindow, QWidget#centralHost {{
        background: {bg};
    }}

    /* ---------- Dialogs / popups: force the light surface ----------
       Windows in dark mode otherwise hands QDialog / QMessageBox a dark
       system palette for sub-widgets (labels, list views, combo popups).
       Pin everything to our light theme explicitly. */
    QDialog, QMessageBox, QInputDialog, QFileDialog, QColorDialog,
    QProgressDialog, QErrorMessage, QFontDialog {{
        background: {bg};
        color: {text};
    }}
    QDialog QLabel, QMessageBox QLabel, QInputDialog QLabel,
    QFileDialog QLabel, QColorDialog QLabel, QProgressDialog QLabel,
    QErrorMessage QLabel, QFontDialog QLabel {{
        color: {text};
        background: transparent;
    }}
    QDialog QFrame, QMessageBox QFrame, QInputDialog QFrame {{
        background: transparent;
    }}
    QDialogButtonBox QPushButton {{
        min-width: 88px;
    }}

    /* Combo popup (QListView inside QComboBox) */
    QComboBox QAbstractItemView {{
        background: {surface};
        color: {text};
        border: 1px solid {border};
        selection-background-color: {accent};
        selection-color: white;
        outline: 0;
    }}
    QAbstractItemView {{
        background: {surface};
        color: {text};
        alternate-background-color: {surface_alt};
        selection-background-color: {accent};
        selection-color: white;
    }}
    /* Tooltip */
    QToolTip {{
        background: #111827;
        color: #F9FAFB;
        border: 1px solid #111827;
        padding: 4px 8px;
    }}

    /* ---------- Menus (containers → square) ---------- */
    QMenuBar {{
        background: {surface};
        border-bottom: 1px solid {border};
        padding: 2px 6px;
    }}
    QMenuBar::item {{
        padding: 6px 10px;
        background: transparent;
        border-radius: 6px;  /* menu item is tag-like */
    }}
    QMenuBar::item:selected {{
        background: {surface_alt};
    }}
    QMenu {{
        background: {surface};
        border: 1px solid {border};
        border-radius: 0;  /* container: square */
        padding: 6px;
    }}
    QMenu::item {{
        padding: 6px 16px;
        border-radius: 6px;  /* tag-like item */
    }}
    QMenu::item:selected {{
        background: {accent};
        color: white;
    }}

    /* ---------- Top bar (square container) ---------- */
    QFrame#TopBar {{
        background: {accent};
        border: none;
    }}
    QFrame#TopBar QLabel {{
        color: white;
        font-weight: 600;
    }}
    QFrame#TopBar QPushButton {{
        background: rgba(255,255,255,0.15);
        color: white;
        border: 1px solid rgba(255,255,255,0.25);
        border-radius: 8px;
        padding: 6px 12px;
    }}
    QFrame#TopBar QPushButton:hover {{
        background: rgba(255,255,255,0.28);
    }}
    QFrame#TopBar QPushButton:pressed {{
        background: rgba(0,0,0,0.15);
    }}

    /* Mode chip: only element whose colour swaps with the mode. */
    QLabel#ModeChip {{
        border-radius: 14px;
        padding: 6px 14px;
        font-weight: 700;
        letter-spacing: 0.4px;
    }}
    QLabel#ModeChip[mode="production"] {{
        background: rgba(255,255,255,0.18);
        color: white;
        border: 1px solid rgba(255,255,255,0.3);
    }}
    QLabel#ModeChip[mode="maintenance"] {{
        background: {maintenance};
        color: {maintenance_fg};
        border: 1px solid {_darken(maintenance, 0.15)};
    }}

    /* ---------- Sidebar: slightly darker than the accent ---------- */
    QFrame#Sidebar {{
        background: {_darken(accent, 0.22)};
        border-right: 1px solid {_darken(accent, 0.4)};
    }}
    QPushButton#SidebarButton {{
        background: transparent;
        border: none;
        padding: 12px 16px;
        text-align: left;
        color: rgba(255,255,255,0.88);
        font-size: 11pt;
        font-weight: 600;
        border-radius: 8px;
    }}
    QPushButton#SidebarButton:hover {{
        background: rgba(255,255,255,0.12);
        color: #FFFFFF;
    }}
    QPushButton#SidebarButton:checked {{
        background: rgba(255,255,255,0.22);
        color: #FFFFFF;
        font-weight: 800;
    }}
    QPushButton#SidebarSubButton {{
        background: transparent;
        border: none;
        padding: 9px 14px 9px 36px;
        text-align: left;
        color: rgba(255,255,255,0.70);
        font-size: 10pt;
        font-weight: 500;
        border-radius: 8px;
    }}
    QPushButton#SidebarSubButton:hover {{
        background: rgba(255,255,255,0.10);
        color: #FFFFFF;
    }}
    QPushButton#SidebarSubButton:checked {{
        background: rgba(255,255,255,0.22);
        color: #FFFFFF;
        font-weight: 700;
    }}

    /* ---------- Cards (containers → square) ---------- */
    QFrame#Card {{
        background: {surface};
        border: 1px solid {border};
        border-radius: 0;
    }}
    QFrame#PageHeader {{
        background: {surface};
        border-bottom: 1px solid {border};
        border-radius: 0;
    }}
    QFrame#PageHeader QLabel#PageTitle {{
        font-size: 16pt;
        font-weight: 700;
    }}
    QLabel#Muted {{
        color: {muted};
    }}

    /* ---------- Top-bar hover flyouts ---------- */
    QFrame#FlyoutPanel {{
        background: {surface};
        border: none;
        border-radius: 10px;
    }}
    QScrollArea#NotificationFlyoutScroll {{
        border: none;
        background: transparent;
    }}
    QScrollArea#NotificationFlyoutScroll > QWidget > QWidget {{
        background: transparent;
    }}
    QListWidget#NotificationFlyoutList {{
        background: transparent;
        border: none;
        border-radius: 0;
        padding: 0;
        outline: none;
    }}
    QListWidget#NotificationFlyoutList::item {{
        border: none;
        border-radius: 0;
        padding: 12px 2px;
        background: transparent;
    }}
    QListWidget#NotificationFlyoutList::item:hover {{
        background: {surface_alt};
    }}
    QToolButton#NotificationClearButton {{
        border: none;
        background: transparent;
        padding: 4px;
        border-radius: 6px;
    }}
    QToolButton#NotificationClearButton:hover {{
        background: {surface_alt};
    }}
    QToolButton#NotificationClearButton:pressed {{
        background: {border};
    }}
    QToolButton#NotificationClearButton:disabled {{
        background: transparent;
    }}

    /* ---------- Buttons (interactive → rounded) ---------- */
    QPushButton.primary, QPushButton[accent="true"] {{
        background: {accent};
        color: white;
        border: none;
        border-radius: 8px;
        padding: 8px 14px;
        font-weight: 600;
    }}
    QPushButton.primary:hover, QPushButton[accent="true"]:hover {{
        background: {accent_hover};
    }}
    QPushButton.primary:pressed, QPushButton[accent="true"]:pressed {{
        background: {accent_pressed};
    }}
    QPushButton {{
        background: {surface};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 7px 12px;
    }}
    QPushButton:hover {{
        background: {surface_alt};
    }}
    QPushButton:disabled {{
        color: {muted};
        background: {surface_alt};
    }}

    /* ---------- Form fields (interactive → rounded) ---------- */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        background: {surface};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 6px 8px;
        selection-background-color: {accent};
        selection-color: white;
    }}
    QComboBox::drop-down {{
        border: none;
        width: 18px;
    }}

    /* ---------- Lists / trees / tables (container → square; items → rounded tag) ---------- */
    QListWidget, QTreeWidget, QTableWidget {{
        background: {surface};
        border: 1px solid {border};
        border-radius: 0;
        padding: 4px;
        outline: 0;  /* kill the dotted focus rectangle */
    }}
    QListWidget::item, QTreeWidget::item {{
        padding: 8px 10px;
        border: none;
        border-radius: 6px;
    }}
    QListWidget::item:selected, QTreeWidget::item:selected {{
        background: {accent};
        color: white;
        border: none;
    }}
    QListWidget::item:selected:active,
    QListWidget::item:selected:!active,
    QListWidget::item:focus,
    QTreeWidget::item:selected:active,
    QTreeWidget::item:selected:!active,
    QTreeWidget::item:focus {{
        background: {accent};
        color: white;
        border: none;
        outline: 0;
    }}

    /* ---------- Scrollbars (handle → rounded) ---------- */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {border};
        border-radius: 4px;
        min-height: 24px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        background: none;
        border: none;
        height: 0px;
    }}

    /* ---------- Status bar / misc ---------- */
    QStatusBar {{
        background: {surface};
        border-top: 1px solid {border};
    }}
    QLabel#Logo {{
        color: white;
        font-size: 13pt;
        font-weight: 800;
        letter-spacing: 1px;
    }}

    /* ---------- Flow nodes (containers → square) ---------- */
    QFrame#FlowNode {{
        background: {surface};
        border: 2px solid {accent};
        border-radius: 0;
        padding: 12px;
    }}
    QLabel#FlowArrow {{
        color: {accent};
        font-size: 20pt;
        font-weight: 700;
    }}
    QLabel#StatusDot[state="ok"] {{ color: #16A34A; }}
    QLabel#StatusDot[state="bad"] {{ color: #DC2626; }}
    QLabel#StatusDot[state="unknown"] {{ color: #9CA3AF; }}
    """


def _build_light_palette() -> QPalette:
    """Complete light palette used by every widget Qt creates.

    Needed because Windows 11 hands Qt a dark system palette when the OS
    is in dark mode, which bleeds into dialog sub-widgets (labels in
    QMessageBox, the item view behind a combo popup, etc.). Pinning every
    palette role avoids any of those widgets rendering dark text on a dark
    surface.
    """
    bg = QColor("#F5F6FA")
    surface = QColor("#FFFFFF")
    surface_alt = QColor("#EEF1F7")
    text = QColor("#1F2937")
    muted = QColor("#6B7280")
    border = QColor("#D7DCE6")
    accent = QColor(ACCENT_PRODUCTION)
    white = QColor("#FFFFFF")
    disabled_text = QColor("#9CA3AF")

    p = QPalette()
    p.setColor(QPalette.Window, bg)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, surface)
    p.setColor(QPalette.AlternateBase, surface_alt)
    p.setColor(QPalette.ToolTipBase, QColor("#111827"))
    p.setColor(QPalette.ToolTipText, QColor("#F9FAFB"))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.PlaceholderText, muted)
    p.setColor(QPalette.Button, surface)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor("#DC2626"))
    p.setColor(QPalette.Link, accent)
    p.setColor(QPalette.LinkVisited, accent)
    p.setColor(QPalette.Highlight, accent)
    p.setColor(QPalette.HighlightedText, white)
    p.setColor(QPalette.Light, surface)
    p.setColor(QPalette.Midlight, surface_alt)
    p.setColor(QPalette.Mid, border)
    p.setColor(QPalette.Dark, border)
    p.setColor(QPalette.Shadow, QColor("#1F2937"))

    # Disabled group — keep surfaces light, mute text.
    for role in (
        QPalette.WindowText,
        QPalette.Text,
        QPalette.ButtonText,
        QPalette.PlaceholderText,
    ):
        p.setColor(QPalette.Disabled, role, disabled_text)
    p.setColor(QPalette.Disabled, QPalette.Window, bg)
    p.setColor(QPalette.Disabled, QPalette.Base, surface_alt)
    p.setColor(QPalette.Disabled, QPalette.Button, surface_alt)
    p.setColor(QPalette.Disabled, QPalette.Highlight, surface_alt)
    p.setColor(QPalette.Disabled, QPalette.HighlightedText, muted)
    return p


def apply_theme(app: QApplication) -> None:
    # The Windows 11 native style follows the OS' dark mode setting for a
    # number of sub-controls we can't fully override via QSS (combo popups,
    # list-view text in QInputDialog.getItem, message box labels, ...).
    # Fusion is palette-driven and consistent across platforms, so we force
    # it on and ship our own light palette.
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    # Respect Qt's own dark-mode opt-out on Windows as a belt-and-braces
    # measure — this stops the platform theme from recolouring title bars
    # / non-client areas before we even get the palette in.
    os.environ.setdefault("QT_QPA_PLATFORM_THEME", "")

    app.setPalette(_build_light_palette())
    app.setStyleSheet(build_qss())
