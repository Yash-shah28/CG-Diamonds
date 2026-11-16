"""Read-only presentation of sync outcomes; queue acceptance is not import completion."""
import json
from pathlib import Path
from .responses import queued_acceptance


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
    from openpyxl import load_workbook
    from PySide6.QtCore import QAbstractTableModel, Qt
    from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox, QTableView)
    from .core import WorkbookValidationError
    from .dialogs import dialog_layout
    from .workbook_errors import workbook_error

    title = f"{profile['kind']} Diamonds"
    try:
        workbook = load_workbook(profile['path'], read_only=True, data_only=True)
        try:
            sheet_name = profile['sheet']
            if sheet_name not in workbook.sheetnames:
                raise WorkbookValidationError(
                    f"Worksheet '{sheet_name}' was not found. Choose the correct worksheet in Excel Mapping."
                )
            sheet = workbook[sheet_name]
            sheet.reset_dimensions()
            rows = list(sheet.values)
        finally:
            workbook.close()
    except Exception as exc:
        QMessageBox.warning(parent, title, workbook_error(profile, exc))
        return

    while rows and all(value is None for value in rows[-1]):
        rows.pop()
    headers = rows.pop(0) if rows else ()
    columns = max((len(row) for row in rows), default=0)
    columns = max(columns, len(headers))

    class InventoryModel(QAbstractTableModel):
        def rowCount(self, parent_index):
            return 0 if parent_index.isValid() else len(rows)

        def columnCount(self, parent_index):
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
    dialog.resize(1100, 650)
    layout = dialog_layout(dialog)
    source = QLabel(f"{Path(profile['path']).name}  ·  {sheet_name}")
    source.setTextFormat(Qt.PlainText)
    layout.addWidget(source)
    if rows:
        table = QTableView(dialog)
        table.setModel(InventoryModel(table))
        table.horizontalHeader().setDefaultSectionSize(160)
        table.setHorizontalScrollMode(QTableView.ScrollPerPixel)
        table.setAlternatingRowColors(True)
        layout.addWidget(table, 1)
    else:
        layout.addWidget(QLabel('This worksheet has no inventory rows.'), 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
