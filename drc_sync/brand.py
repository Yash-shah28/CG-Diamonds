"""Chintamani Gems visual identity; storage and credential identities stay compatible."""
from pathlib import Path
from PySide6.QtGui import QIcon, QPixmap

ASSETS = Path(__file__).resolve().parent / 'assets'

def app_icon():
    return QIcon(str(ASSETS / 'chintamani-gems.ico'))

def brand_pixmap():
    return QPixmap(str(ASSETS / 'chintamani-gems.png'))

STYLE = """
QMainWindow, QDialog, QMessageBox { background: #f5f7fa; }
QWidget { font-family: 'Segoe UI'; font-size: 15px; color: #202b3a; }
QLabel { background: transparent; border: none; padding: 0; }
QLabel#brandName { font-size: 16px; font-weight: 600; }
QLabel#pageTitle { font-size: 24px; font-weight: 600; }
QLabel#muted { color: #687585; font-size: 13px; }
QLabel#stat { font-size: 26px; font-weight: 600; }
QLabel#badge { background: #edf6f0; color: #38634c; border: 1px solid #d9e9df; padding: 5px 10px; border-radius: 5px; font-size: 13px; }
QLabel#fileInfo { background: #f7f9fb; color: #475569; border: 1px solid #e1e6ed; padding: 14px; border-radius: 5px; }
QLabel#error { color: #a33b35; }
QFrame#card { background: white; border: 1px solid #e0e5eb; border-radius: 7px; }
QFrame#sidebar { background: white; border-right: 1px solid #e0e5eb; }
QToolButton#nav { background: transparent; border: none; border-radius: 5px; padding: 12px; font-size: 14px; text-align: left; color: #526172; }
QToolButton#nav:hover { background: #f0f4fa; }
QToolButton#nav:checked { background: #eaf4ff; color: #126bc4; border: 1px solid #cde4ff; }
QPushButton { background: #2f6ae6; color: white; border: 1px solid #2f6ae6; border-radius: 5px; padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #255bc9; }
QPushButton:disabled { background: #e7ebf1; color: #8b96a5; border-color: #e0e5eb; }
QPushButton#quiet { background: white; color: #475569; border-color: #d8e0e9; }
QPushButton#quiet:hover { background: #f2f5f9; }
QPlainTextEdit, QLineEdit, QComboBox, QDoubleSpinBox { background: white; color: #202b3a; border: 1px solid #d6dee8; border-radius: 4px; padding: 7px; selection-background-color: #dbe8ff; selection-color: #202b3a; }
QLineEdit#percentage { background: white; border: 1px solid #8295ac; border-radius: 5px; padding: 7px 12px; font-size: 16px; font-weight: 600; color: #172a42; }
QLineEdit#percentage:focus { border: 2px solid #2f6ae6; }
QPushButton#adjust { background: white; color: #485b73; border: 1px solid #cbd5e1; font-size: 22px; padding: 0; font-weight: 400; }
QPushButton#adjust:checked { background: #2f6ae6; border-color: #2f6ae6; color: white; }
QPushButton#adjust:hover { border-color: #2f6ae6; }
QLabel#dialogTitle { font-size: 20px; font-weight: 600; }
QDialog { background: white; }
QDialogButtonBox QPushButton { min-width: 84px; }
QComboBox QLineEdit { border: none; padding: 0; margin: 0; background: transparent; color: #202b3a; }
QComboBox:disabled { color: #526172; background: #f3f5f7; }
QComboBox QAbstractItemView { background: white; color: #202b3a; selection-background-color: #e9f0ff; selection-color: #202b3a; }
QComboBox::drop-down { border: none; width: 22px; }
QTableWidget { background: white; alternate-background-color: white; color: #344256; border: 1px solid #e1e6ed; border-radius: 5px; gridline-color: #dce4ed; selection-background-color: #e9f0ff; selection-color: #202b3a; }
QHeaderView::section { background: #edf0f3; color: #253244; border: none; border-bottom: 1px solid #e1e6ed; padding: 14px 12px; font-weight: 600; font-size: 13px; }
QTableWidget::item { border: none; padding: 9px; }
QTableWidget#mappingTable::item { padding: 0; }
QScrollBar:horizontal { background: #f0f3f7; height: 9px; margin: 0; }
QScrollBar::handle:horizontal { background: #c7d0dc; min-width: 24px; border-radius: 4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar:vertical { background: #f4f6f9; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #c7d0dc; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenu { background: white; border: 1px solid #d8e0e9; }
QMenu::item { padding: 9px 20px; }
QMenu::item:selected { background: #e9f0ff; }
"""

STYLE += 'QComboBox::down-arrow { image: url("' + (ASSETS / "chevron-down.png").as_posix() + '"); width: 12px; height: 12px; }'
