from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette


COLORS = {
    "window": "#17120D",
    "surface": "#211A12",
    "surface_alt": "#2E2418",
    "border": "#A08A68",
    "text": "#FFF4DE",
    "muted": "#D5C3A2",
    "accent": "#E7B85C",
    "accent_dark": "#6B4D1E",
    "accent_soft": "#3C2D18",
    "accent_text": "#FFE2A6",
    "on_accent": "#1B1207",
    "green": "#72D5A4",
    "green_dark": "#176B4C",
    "amber": "#F39C5A",
    "red": "#FF8378",
    "selection": "#6A4A22",
    "hover": "#3A2D1E",
    "disabled_text": "#A99B82",
    "disabled_surface": "#1B1712",
    "disabled_border": "#594C39",
    # Review-diff pair, matching Android's MainActivity exactly so a correction
    # reads the same on both. Insertions were the app's accent gold, which a
    # reader has to be told means "added"; green and struck-through salmon say
    # it without a legend.
    "diff_insert_background": "#77E2AC",  # Android Color.rgb(119, 226, 172)
    "diff_insert_text": "#0D1C16",  # Android Color.rgb(13, 28, 22)
    "diff_remove_background": "#4A2024",
    "diff_remove_text": "#FF978B",  # Android Color.rgb(255, 151, 139)
}


def apply_dark_theme(application) -> None:
    application.setFont(QFont(_load_windows_ui_font(), 10))
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["window"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["surface_alt"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["surface_alt"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["surface_alt"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["selection"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["muted"]))
    application.setPalette(palette)
    application.setStyleSheet(STYLESHEET)


def _load_windows_ui_font() -> str:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    font_path = windows / "Fonts" / "segoeui.ttf"
    if font_path.is_file():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            return families[0]
    return "Segoe UI"


STYLESHEET = f"""
* {{
    font-family: "Segoe UI";
    font-size: 10pt;
    letter-spacing: 0px;
}}
QMainWindow, QDialog {{
    background: {COLORS['window']};
    color: {COLORS['text']};
}}
QWidget {{
    color: {COLORS['text']};
}}
QLabel#appTitle {{
    font-size: 21pt;
    font-weight: 700;
}}
QLabel#appSubtitle, QLabel#mutedLabel {{
    color: {COLORS['muted']};
}}
QLabel#statusText {{
    font-size: 15pt;
    font-weight: 650;
}}
QLabel#sectionTitle {{
    font-size: 13pt;
    font-weight: 650;
}}
QLabel#privacyBadge {{
    min-height: 28px;
    padding: 0 10px;
    border: 1px solid {COLORS['accent']};
    border-radius: 14px;
    background: {COLORS['accent_soft']};
    color: {COLORS['accent_text']};
    font-size: 9pt;
    font-weight: 700;
}}
QFrame#headerBand, QFrame#statusBand, QFrame#entryBand {{
    background: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 9px;
}}
QFrame#stateDot {{
    background: {COLORS['green']};
    border-radius: 6px;
    min-width: 12px;
    max-width: 12px;
    min-height: 12px;
    max-height: 12px;
}}
QPushButton {{
    min-height: 36px;
    padding: 0 14px;
    border: 1px solid {COLORS['border']};
    border-radius: 7px;
    background: {COLORS['surface_alt']};
    color: {COLORS['text']};
}}
QPushButton:hover {{
    border-color: {COLORS['accent']};
    background: {COLORS['hover']};
}}
QPushButton:focus {{
    border: 2px solid {COLORS['accent']};
}}
QPushButton:disabled {{
    color: {COLORS['disabled_text']};
    background: {COLORS['disabled_surface']};
    border-color: {COLORS['disabled_border']};
}}
QPushButton#primaryButton {{
    min-height: 46px;
    font-size: 11pt;
    font-weight: 700;
    background: {COLORS['accent']};
    border-color: {COLORS['accent_text']};
    color: {COLORS['on_accent']};
}}
QPushButton#primaryButton:hover {{
    background: {COLORS['accent_text']};
    border-color: {COLORS['text']};
}}
QPushButton#primaryButton:disabled {{
    color: {COLORS['disabled_text']};
    background: {COLORS['disabled_surface']};
    border-color: {COLORS['disabled_border']};
}}
QPushButton#dangerButton {{
    color: #FFE3E0;
    border-color: {COLORS['red']};
}}
QPushButton[segment="true"] {{
    min-width: 104px;
    min-height: 34px;
    padding: 0 10px;
}}
QPushButton[segment="true"]:checked {{
    background: {COLORS['accent_dark']};
    border-color: {COLORS['accent']};
    font-weight: 650;
}}
QListWidget#sectionNav {{
    min-width: 142px;
    max-width: 142px;
    padding: 5px;
    border: 1px solid {COLORS['border']};
    border-radius: 9px;
    background: {COLORS['surface']};
    outline: none;
}}
QListWidget#sectionNav::item {{
    min-height: 30px;
    padding: 0 11px;
    margin: 0;
    border: 1px solid transparent;
    border-radius: 7px;
    color: {COLORS['muted']};
}}
QListWidget#sectionNav::item:hover {{
    color: {COLORS['text']};
    background: {COLORS['surface_alt']};
    border-color: {COLORS['border']};
}}
QListWidget#sectionNav::item:selected {{
    color: {COLORS['text']};
    background: {COLORS['accent_dark']};
    border-color: {COLORS['accent']};
    font-weight: 700;
}}
QProgressBar {{
    min-height: 8px;
    max-height: 8px;
    border: 0;
    border-radius: 4px;
    background: {COLORS['hover']};
    text-align: center;
}}
QProgressBar::chunk {{
    background: {COLORS['accent']};
    border-radius: 4px;
}}
QTabWidget::pane {{
    border: 0;
    top: -1px;
}}
QTabBar::tab {{
    min-height: 36px;
    padding: 0 12px;
    color: {COLORS['muted']};
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}}
QTabBar::tab:selected {{
    color: {COLORS['text']};
    background: {COLORS['surface_alt']};
    border-color: {COLORS['accent']};
}}
QTabBar::tab:focus {{
    border: 2px solid {COLORS['accent']};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTableWidget {{
    min-height: 34px;
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    background: {COLORS['surface']};
    color: {COLORS['text']};
    selection-background-color: {COLORS['selection']};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    padding: 0 9px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTableWidget:focus {{
    border: 2px solid {COLORS['accent']};
}}
QPlainTextEdit {{
    padding: 10px;
    font-size: 11pt;
}}
QTableWidget {{
    gridline-color: {COLORS['border']};
    alternate-background-color: {COLORS['surface_alt']};
}}
QHeaderView::section {{
    min-height: 34px;
    padding: 0 8px;
    border: 0;
    border-right: 1px solid {COLORS['border']};
    border-bottom: 1px solid {COLORS['border']};
    background: {COLORS['surface_alt']};
    color: {COLORS['text']};
    font-weight: 650;
}}
QGroupBox {{
    margin-top: 18px;
    padding: 16px 12px 12px 12px;
    border: 1px solid {COLORS['border']};
    border-radius: 5px;
    font-weight: 650;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}}
QCheckBox {{
    min-height: 32px;
    spacing: 8px;
}}
QCheckBox:focus {{
    color: {COLORS['accent_text']};
}}
QListWidget#sectionNav:focus {{
    border: 2px solid {COLORS['accent']};
}}
QScrollArea {{
    border: 0;
    background: transparent;
}}
QScrollBar:vertical {{
    width: 12px;
    margin: 0;
    background: {COLORS['window']};
}}
QScrollBar::handle:vertical {{
    min-height: 32px;
    border-radius: 5px;
    background: {COLORS['border']};
}}
QScrollBar::handle:vertical:hover {{
    background: {COLORS['accent']};
}}
/* Without these the default arrow buttons take the top and bottom of the
   track, which on a short review box leaves no room for a visible handle. */
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    border: 0;
    background: none;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
QToolTip {{
    padding: 6px;
    border: 1px solid {COLORS['border']};
    background: {COLORS['surface_alt']};
    color: {COLORS['text']};
}}
"""
