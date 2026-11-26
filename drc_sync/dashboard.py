"""Read-only presentation of sync outcomes; queue acceptance is not import completion."""
import json
from pathlib import Path
import time
from .responses import queued_acceptance


def history_cutoff():
    return time.time() - 3 * 24 * 60 * 60


def result_label(row):
    try:
        body = json.loads(row['response_body'] or '{}')
    except (ValueError, TypeError):
        body = {}
    if isinstance(body, dict):
        if body.get('success') is False or body.get('errors') or body.get('failedItems'):
            return 'Failure'
        if queued_acceptance(row['response_status'], row['response_body']):
            return 'Sent to DRC'
    return {'sent': 'Sent to DRC', 'success': 'Success', 'failed': 'Failure', 'blocked': 'Blocked',
            'review': 'Needs review', 'retry': 'Failure'}.get(row['state'], row['state'].capitalize())


def history_entries(rows, profiles):
    by_id = {p['id']: p for p in profiles}
    entries = []
    for row in rows:
        profile = by_id.get(row['profile'], {})
        entries.append(dict(id=row['id'], created=row['created'],
            kind=row['response_kind'] or row['kind'] or profile.get('kind', row['profile']),
            filename=Path(row['response_filename'] or row['filename'] or profile.get('path', '')).name,
            records=row['records'], result=result_label(row), detail=row['detail']))
    return entries


def show_workbook_inventory(parent, profile):
    from decimal import Decimal, InvalidOperation
    from openpyxl import load_workbook
    from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
    from PySide6.QtGui import QShortcut
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QPushButton, QTableView, QVBoxLayout
    from .contract import suggested_field
    from .core import WorkbookValidationError
    from .workbook_errors import workbook_error
    from .inventory_edit_ui import import_hrd_report, save_excel_copy
    from .inventory_excel import workbook_stamp

    title = f"{profile['kind']} Diamonds"
    def read_table():
        stamp = workbook_stamp(profile['path'])
        workbook = load_workbook(profile['path'], read_only=True, data_only=True)
        try:
            sheet_name = profile['sheet']
            if sheet_name not in workbook.sheetnames:
                raise WorkbookValidationError(
                    f"Worksheet '{sheet_name}' was not found. Choose the correct worksheet in Excel Mapping."
                )
            sheet = workbook[sheet_name]
            sheet.reset_dimensions()
            source_rows = sheet.iter_rows()
            headers = tuple(cell.value for cell in next(source_rows, ()))
            discount_columns = {
                index for index, header in enumerate(headers)
                if suggested_field(header) == 'Discount'
                or profile.get('mapping', {}).get(str(header).strip()) == 'Discount'
                or str(header).strip().casefold() == 'rapaport trade discount'
            }

            def display_discount(cell):
                value = cell.value
                if value is None or isinstance(value, bool):
                    return value
                text = str(value).strip()
                try:
                    number = Decimal(text.removesuffix('%').strip())
                except InvalidOperation:
                    return value
                if not number.is_finite():
                    return value
                # Excel percentages are stored as fractions. Plain exports can use points.
                if not text.endswith('%') and ('%' in (cell.number_format or '') or abs(number) <= 1):
                    number *= 100
                return f'{number:.2f}%'

            rows = [
                tuple(display_discount(cell) if index in discount_columns else cell.value
                      for index, cell in enumerate(row))
                for row in source_rows
            ]
        finally:
            workbook.close()
        while rows and all(value is None for value in rows[-1]):
            rows.pop()
        columns = max((len(row) for row in rows), default=0)
        columns = max(columns, len(headers))

        if workbook_stamp(profile['path']) != stamp:
            raise WorkbookValidationError('Excel changed while loading the inventory. Try again.')
        return headers, rows, columns, discount_columns

    try:
        headers, rows, columns, discount_columns = read_table()
    except Exception as exc:
        QMessageBox.warning(parent, title, workbook_error(profile, exc))
        return

    class InventoryModel(QAbstractTableModel):
        def rowCount(self, parent_index=QModelIndex()):
            return 0 if parent_index.isValid() else len(rows)

        def columnCount(self, parent_index=QModelIndex()):
            return 0 if parent_index.isValid() else columns

        def data(self, index, role):
            if index.isValid() and role == Qt.DisplayRole:
                row = rows[index.row()]
                value = row[index.column()] if index.column() < len(row) else None
                return '' if value is None else str(value)

        def headerData(self, section, orientation, role):
            if role == Qt.DisplayRole:
                if orientation == Qt.Vertical:
                    return str(section + 2)
                value = headers[section] if section < len(headers) else None
                return '' if value is None else str(value)

    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setWindowFlags(
        Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
        | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
    )
    dialog.resize(1100, 650)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(0, 0, 0, 0)

    previous_window_state = Qt.WindowNoState

    def toggle_full_screen():
        nonlocal previous_window_state
        if dialog.isFullScreen():
            dialog.setWindowState(previous_window_state)
        else:
            previous_window_state = dialog.windowState()
            dialog.showFullScreen()

    shortcut = QShortcut('F11', dialog)
    shortcut.activated.connect(toggle_full_screen)

    table = QTableView(dialog)
    model = InventoryModel(table)
    table.setModel(model)
    table.setHorizontalScrollMode(QTableView.ScrollPerPixel)
    table.setAlternatingRowColors(True)
    table.setStyleSheet('''
        QTableView {
            background: #ffffff;
            alternate-background-color: #f8fafc;
            color: #0f172a;
            selection-background-color: #dbeafe;
            selection-color: #0f172a;
            gridline-color: #e2e8f0;
            border: none;
        }
        QTableCornerButton::section { background: #f1f5f9; border: none; }
    ''')
    header = table.horizontalHeader()
    header.setDefaultSectionSize(160)
    row_header = table.verticalHeader()
    row_header.setStyleSheet(
        'QHeaderView::section { color: #1e293b; background: #f1f5f9; padding: 0 10px; }'
    )

    for index in discount_columns:
        caption_width = header.fontMetrics().horizontalAdvance(str(headers[index]))
        table.setColumnWidth(index, max(160, caption_width + 55))
    row_header.setMinimumWidth(row_header.fontMetrics().horizontalAdvance(str(len(rows) + 1)) + 26)

    def import_report():
        nonlocal headers, rows, columns, discount_columns
        if not import_hrd_report(dialog, profile):
            return
        try:
            updated = read_table()
        except Exception as exc:
            QMessageBox.warning(dialog, title, workbook_error(profile, exc))
            return
        model.beginResetModel()
        headers, rows, columns, discount_columns = updated
        model.endResetModel()

    toolbar = QHBoxLayout()
    toolbar.setContentsMargins(12, 6, 12, 6)
    download_button = QPushButton('Download Excel')
    download_button.setObjectName('quiet')
    download_button.clicked.connect(lambda: save_excel_copy(dialog, profile))
    import_button = QPushButton('Import HRD report')
    import_button.setObjectName('quiet')
    import_button.clicked.connect(import_report)
    toolbar.addStretch()
    toolbar.addWidget(import_button)
    toolbar.addWidget(download_button)
    layout.addLayout(toolbar)
    layout.addWidget(table, 1)
    dialog.exec()
