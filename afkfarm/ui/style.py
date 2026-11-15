"""Тёмная тема оформления."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#d9a441"      # золото — как интерфейс игры
BG = "#17181c"
BG2 = "#1f2127"
BG3 = "#2a2d35"
FG = "#e6e6e6"
FG_DIM = "#9aa0a6"
RED = "#e05252"
BLUE = "#4f8fe6"
GREEN = "#4caf6a"
ORANGE = "#e08a3c"

QSS = f"""
QWidget {{ font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QTabWidget::pane {{ border: 1px solid {BG3}; border-radius: 6px; top: -1px; background: {BG}; }}
QTabBar::tab {{ background: {BG2}; color: {FG_DIM}; padding: 8px 16px; border: 1px solid {BG3};
    border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {BG}; color: {ACCENT}; font-weight: bold; }}
QTabBar::tab:hover {{ color: {FG}; }}
QGroupBox {{ border: 1px solid {BG3}; border-radius: 8px; margin-top: 14px; padding: 10px 8px 8px 8px;
    background: {BG2}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {ACCENT};
    font-weight: bold; }}
QPushButton {{ background: {BG3}; color: {FG}; border: 1px solid #3a3e48; border-radius: 6px;
    padding: 6px 12px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #34373f; }}
QPushButton:disabled {{ color: #5d6168; border-color: #2c2f36; }}
QPushButton:checked {{ background: #4a3a1c; border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#startButton {{ background: #1f4d2c; border-color: #2e7d45; font-size: 13pt;
    font-weight: bold; padding: 10px 18px; }}
QPushButton#startButton:hover {{ background: #26603a; }}
QPushButton#pauseButton {{ background: #4d421f; border-color: #8a7330; font-size: 13pt;
    font-weight: bold; padding: 10px 18px; }}
QPushButton#pauseButton:hover {{ background: #5e5126; }}
QPushButton#stopButton {{ background: #5a2222; border-color: #8f3434; font-size: 13pt;
    font-weight: bold; padding: 10px 18px; }}
QPushButton#stopButton:hover {{ background: #6d2929; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTableWidget, QListWidget {{
    background: {BG}; color: {FG}; border: 1px solid #353944; border-radius: 5px; padding: 3px 5px;
    selection-background-color: #5a4720; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#keyEdit {{ font-weight: bold; color: {ACCENT}; }}
QLineEdit#keyEdit[invalid="true"] {{ border-color: {RED}; color: {RED}; }}
QHeaderView::section {{ background: {BG3}; color: {FG_DIM}; border: none; padding: 5px; }}
QTableWidget {{ gridline-color: #2c2f36; }}
QProgressBar {{ border: 1px solid #353944; border-radius: 5px; background: #101114; text-align: center;
    color: white; font-weight: bold; min-height: 20px; }}
QProgressBar#hpBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ff6b6b, stop:1 #a42020);
    border-radius: 4px; }}
QProgressBar#mpBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #6fb0ff, stop:1 #1f5bb5);
    border-radius: 4px; }}
QProgressBar#targetBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffae5c, stop:1 #b5561f);
    border-radius: 4px; }}
QLabel#statValue {{ font-size: 15pt; font-weight: bold; color: {FG}; }}
QLabel#statName {{ color: {FG_DIM}; }}
QLabel#bigState {{ font-size: 16pt; font-weight: bold; }}
QLabel#hint {{ color: {FG_DIM}; }}
QFrame#card {{ background: {BG2}; border: 1px solid {BG3}; border-radius: 8px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {BG3}; color: {FG}; border: 1px solid {ACCENT}; padding: 4px; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid #6b7078; border-radius: 3px;
    background: #101114; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:disabled {{ border-color: #3a3e48; }}
QMenuBar {{ background: {BG2}; color: {FG}; }}
QMenuBar::item:selected {{ background: {BG3}; }}
QMenu {{ background: {BG2}; color: {FG}; border: 1px solid {BG3}; }}
QMenu::item:selected {{ background: #4a3a1c; }}
QStatusBar {{ background: {BG2}; color: {FG_DIM}; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(FG))
    pal.setColor(QPalette.Base, QColor(BG))
    pal.setColor(QPalette.AlternateBase, QColor(BG2))
    pal.setColor(QPalette.ToolTipBase, QColor(BG3))
    pal.setColor(QPalette.ToolTipText, QColor(FG))
    pal.setColor(QPalette.Text, QColor(FG))
    pal.setColor(QPalette.Button, QColor(BG3))
    pal.setColor(QPalette.ButtonText, QColor(FG))
    pal.setColor(QPalette.BrightText, QColor(RED))
    pal.setColor(QPalette.Highlight, QColor("#5a4720"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.PlaceholderText, QColor("#6b7078"))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#5d6168"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5d6168"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
