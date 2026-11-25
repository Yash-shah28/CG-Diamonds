"""Import HRD diamond reports and download inventory workbooks."""
import json
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Slot
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QWidget,
)

from . import portal_secrets
from .dialogs import dialog_layout
from .inventory_excel import download_inventory, read_inventory_row, row_values, save_inventory_row
from .lab_reports import ReportLookupError, lookup_report, report_number
from .workbook_errors import workbook_error


_HRD_CREDENTIAL = 'lab-report-hrd'


def _credentials():
    stored = portal_secrets.get_secret(_HRD_CREDENTIAL)
    return json.loads(stored) if stored else {}


def _lookup_settings(parent):
    dialog = QDialog(parent)
    dialog.setWindowTitle('HRD lookup settings')
    dialog.resize(520, 220)
    layout = dialog_layout(dialog)
    form = QFormLayout()
    layout.addLayout(form)
    status = QLabel()
    status.setWordWrap(True)
    layout.addWidget(status)
    try:
        existing = _credentials()
    except Exception:
        status.setText('Could not read Windows Credential Manager.')
        existing = {}
    key = QLineEdit()
    key.setEchoMode(QLineEdit.Password)
    if existing.get('key'):
        key.setPlaceholderText('API credential saved')
    form.addRow('HRD API token', key)
    buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    layout.addWidget(buttons)

    def save():
        try:
            token = key.text().strip() or existing.get('key', '')
            if not token:
                raise ReportLookupError('Enter your HRD API token.')
            portal_secrets.put_secret(_HRD_CREDENTIAL, json.dumps({'key': token}))
        except ReportLookupError as error:
            status.setText(str(error))
        except Exception:
            status.setText('Could not save the credential in Windows Credential Manager.')
        else:
            dialog.accept()

    buttons.accepted.connect(save)
    buttons.rejected.connect(dialog.reject)
    dialog.exec()


class _ReportRequest(QThread):
    def __init__(self, number, credentials, parent):
        super().__init__(parent)
        self.number = number
        self.credentials = credentials
        self.result = None
        self.error = ''

    def run(self):
        try:
            self.result = lookup_report('HRD', self.number, self.credentials)
        except ReportLookupError as error:
            self.error = str(error)
        except Exception:
            self.error = 'HRD returned an unexpected response.'
        finally:
            self.credentials = {}


class HrdImportDialog(QDialog):
    def __init__(self, parent, profile, stamp, columns, stock_ids):
        super().__init__(parent)
        self.profile = profile
        self.stamp = stamp
        self.columns = columns
        self.stock_ids = stock_ids
        self.request = None
        self.fetched_number = None
        self.setWindowTitle('Import HRD report')
        self.resize(680, 720)
        layout = dialog_layout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.number = QLineEdit()
        self.number.setPlaceholderText('Number printed on the HRD report')
        form.addRow('HRD report number', self.number)

        lookup_row = QHBoxLayout()
        self.lookup_button = QPushButton('Fetch report')
        self.settings_button = QPushButton('HRD token settings')
        self.settings_button.setObjectName('quiet')
        lookup_row.addWidget(self.lookup_button)
        lookup_row.addWidget(self.settings_button)
        lookup_row.addStretch()
        layout.addLayout(lookup_row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        fields = QFormLayout(content)
        self.inputs = {}
        for column in columns:
            if column.field in ('Lab', 'Certificate #'):
                continue
            entry = QLineEdit(column.text)
            entry.setReadOnly(column.formula)
            label = f'{column.header} (%)' if column.percent and '%' not in column.header else column.header
            fields.addRow(label, entry)
            self.inputs[column.index] = entry
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        if any(column.formula for column in columns):
            formula_note = QLabel('Open and save the workbook in Excel to recalculate formulas after editing their inputs.')
            formula_note.setWordWrap(True)
            layout.addWidget(formula_note)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.Save)
        self.save_button.setText('Save to Excel')
        self.save_button.setEnabled(False)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.lookup_button.clicked.connect(self.begin_lookup)
        self.settings_button.clicked.connect(lambda: _lookup_settings(self))
        if not {'Lab', 'Certificate #'} <= {column.field for column in columns}:
            self.lookup_button.setEnabled(False)
            self.status.setText('This worksheet needs Lab and Report # columns to import an HRD report.')

    def begin_lookup(self):
        if self.request is not None or self.fetched_number is not None:
            return
        try:
            number = report_number(self.number.text())
            credentials = _credentials()
            if not credentials.get('key'):
                raise ReportLookupError('Add your HRD API token under HRD token settings first.')
        except ReportLookupError as error:
            self.status.setText(str(error))
            return
        except Exception:
            self.status.setText('Could not read the HRD API token from Windows Credential Manager.')
            return

        self.request = _ReportRequest(number, credentials, self)
        self.request.finished.connect(self.finish_lookup)
        self.status.setText('Fetching HRD report…')
        for widget in (self.number, self.lookup_button, self.settings_button):
            widget.setEnabled(False)
        self.request.start()

    @Slot()
    def finish_lookup(self):
        request = self.request
        self.request = None
        self.settings_button.setEnabled(True)
        if request.error:
            self.status.setText(request.error)
        else:
            kind, details = request.result
            if kind and kind != self.profile['kind']:
                self.status.setText(f'The report is for a {kind} diamond. Open the {kind} workbook to import it.')
            else:
                self.fetched_number = request.number
                self.number.setText(request.number)
                for column in self.columns:
                    entry = self.inputs.get(column.index)
                    if entry is not None and not column.formula and column.field in details:
                        entry.setText(details[column.field])
                self.save_button.setEnabled(True)
                self.status.setText('Review the grades and fill in any missing fields before saving.')
        if self.fetched_number is None:
            self.number.setEnabled(True)
            self.lookup_button.setEnabled(True)
        request.deleteLater()

    def reject(self):
        if self.request is not None:
            self.status.setText('Wait for the lookup to finish before closing the form.')
            return
        super().reject()

    def save(self):
        if self.fetched_number is None:
            return
        values_by_field = {'Lab': 'HRD', 'Certificate #': self.fetched_number}
        text = {}
        for column in self.columns:
            entry = self.inputs.get(column.index)
            text[column.index] = entry.text() if entry is not None else values_by_field[column.field]
        try:
            values = row_values(self.columns, text, self.stock_ids, adding=True)
            save_inventory_row(self.profile, None, values, self.stamp)
        except Exception as error:
            self.status.setText(workbook_error(self.profile, error))
        else:
            self.accept()


def import_hrd_report(parent, profile):
    try:
        stamp, columns, stock_ids = read_inventory_row(profile)
        dialog = HrdImportDialog(parent, profile, stamp, columns, stock_ids)
        return dialog.exec() == QDialog.Accepted
    except Exception as error:
        QMessageBox.warning(parent, 'Could not open HRD import', workbook_error(profile, error))
        return False


def save_excel_copy(parent, profile):
    filename = f"{profile['kind']} Inventory.xlsx"
    destination, _ = QFileDialog.getSaveFileName(parent, 'Download inventory Excel', filename, 'Excel workbook (*.xlsx)')
    if not destination:
        return
    path = Path(destination)
    if not path.suffix:
        path = path.with_suffix('.xlsx')
    try:
        download_inventory(profile, path)
    except Exception as error:
        QMessageBox.warning(parent, 'Could not download Excel', workbook_error(profile, error))

