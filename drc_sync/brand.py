"""Chintamani Gems visual identity; storage and credential identities stay compatible."""
from pathlib import Path
from PySide6.QtGui import QIcon, QPixmap

ASSETS = Path(__file__).resolve().parent / 'assets'

def app_icon():
    return QIcon(str(ASSETS / 'chintamani-gems.ico'))

def brand_pixmap():
    return QPixmap(str(ASSETS / 'chintamani-gems.png'))

STYLE = """
QMainWindow, QDialog, QMessageBox { background: #f8fafc; }
QWidget { font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; font-size: 14px; color: #0f172a; }
QLabel { background: transparent; border: none; padding: 0; }
QLabel#brandName { font-size: 16px; font-weight: 700; color: #0f172a; }
QLabel#pageTitle { font-size: 24px; font-weight: 700; color: #0f172a; }
QLabel#muted { color: #64748b; font-size: 13px; }
QLabel#stat { font-size: 28px; font-weight: 700; color: #0f172a; }
QLabel#badge { background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
QLabel#fileInfo { background: #f8fafc; color: #334155; border: 1px solid #e2e8f0; padding: 14px; border-radius: 8px; }
QLabel#error { color: #dc2626; font-size: 13px; font-weight: 500; }
QFrame#card { background: white; border: 1px solid #e2e8f0; border-radius: 10px; }
QFrame#sidebar { background: white; border-right: 1px solid #e2e8f0; }
QToolButton#nav { background: transparent; border: none; border-radius: 8px; padding: 12px 14px; font-size: 14px; font-weight: 500; text-align: left; color: #475569; }
QToolButton#nav:hover { background: #f1f5f9; color: #0f172a; }
QToolButton#nav:checked { background: #eff6ff; color: #2563eb; font-weight: 600; border-left: 3px solid #2563eb; }
QPushButton { background: #2563eb; color: white; border: 1px solid #2563eb; border-radius: 6px; padding: 9px 16px; font-weight: 600; font-size: 13px; }
QPushButton:hover { background: #1d4ed8; }
QPushButton:disabled { background: #f1f5f9; color: #94a3b8; border-color: #e2e8f0; }
QPushButton#quiet { background: white; color: #334155; border: 1px solid #cbd5e1; border-radius: 6px; padding: 9px 14px; }
QPushButton#quiet:hover { background: #f8fafc; border-color: #94a3b8; }
QPlainTextEdit, QLineEdit, QComboBox, QDoubleSpinBox { background: white; color: #0f172a; border: 1px solid #cbd5e1; border-radius: 6px; padding: 8px 10px; selection-background-color: #dbeafe; selection-color: #0f172a; }
QLineEdit#percentage { background: white; border: 1px solid #94a3b8; border-radius: 6px; padding: 7px 12px; font-size: 16px; font-weight: 600; color: #0f172a; }
QLineEdit#percentage:focus { border: 2px solid #2563eb; }
QPushButton#adjust { background: white; color: #475569; border: 1px solid #cbd5e1; font-size: 20px; padding: 0; font-weight: 600; border-radius: 6px; }
QPushButton#adjust:checked { background: #2563eb; border-color: #2563eb; color: white; }
QPushButton#adjust:hover { border-color: #2563eb; }
QLabel#dialogTitle { font-size: 18px; font-weight: 700; color: #0f172a; }
QDialog { background: white; }
QDialogButtonBox QPushButton { min-width: 84px; }
QComboBox QLineEdit { border: none; padding: 0; margin: 0; background: transparent; color: #0f172a; }
QComboBox:disabled { color: #94a3b8; background: #f8fafc; }
QComboBox QAbstractItemView { background: white; color: #0f172a; selection-background-color: #eff6ff; selection-color: #0f172a; }
QComboBox::drop-down { border: none; width: 22px; }
QTableWidget { background: white; alternate-background-color: #f8fafc; color: #1e293b; border: 1px solid #e2e8f0; border-radius: 8px; gridline-color: transparent; selection-background-color: #eff6ff; selection-color: #0f172a; }
QHeaderView::section { background: #f1f5f9; color: #334155; border: none; border-bottom: 1px solid #e2e8f0; padding: 12px 10px; font-weight: 600; font-size: 13px; }
QTableWidget::item { border: none; padding: 8px 10px; }
QTableWidget#mappingTable::item { padding: 0; }
QScrollBar:horizontal { background: #f1f5f9; height: 8px; margin: 0; }
QScrollBar::handle:horizontal { background: #cbd5e1; min-width: 24px; border-radius: 4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar:vertical { background: #f1f5f9; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #cbd5e1; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenu { background: white; border: 1px solid #e2e8f0; border-radius: 6px; padding: 4px; }
QMenu::item { padding: 8px 16px; border-radius: 4px; }
QMenu::item:selected { background: #eff6ff; color: #2563eb; }
"""

STYLE += 'QComboBox::down-arrow { image: url("' + (ASSETS / "chevron-down.png").as_posix() + '"); width: 12px; height: 12px; }'
