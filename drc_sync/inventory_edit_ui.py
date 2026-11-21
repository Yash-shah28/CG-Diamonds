"""Dialogs for looking up certificates and saving or downloading inventory."""
import json
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QWidget,
)

from . import portal_secrets
from .dialogs import dialog_layout
from .inventory_excel import download_inventory, read_inventory_row, row_values, save_inventory_row
from .lab_reports import LABS, ReportLookupError, gia_endpoint, lookup_report, report_check_url, report_number
from .workbook_errors import workbook_error


def _credentials(lab):
    stored = portal_secrets.get_secret(f'lab-report-{lab.lower()}')
    return json.loads(stored) if stored else {}


def _lookup_settings(parent, lab):
    dialog = QDialog(parent)
    dialog.setWindowTitle(f'{lab} lookup settings')
    dialog.resize(520, 220)
    layout = dialog_layout(dialog)
    form = QFormLayout()
    layout.addLayout(form)
    status = QLabel()
    status.setWordWrap(True)
    layout.addWidget(status)
    try:
        existing = _credentials(lab)
    except Exception:
        status.setText('Could not read Windows Credential Manager.')
        existing = {}
    url = QLineEdit(existing.get('url', ''))
    if lab == 'GIA':
        url.setPlaceholderText('HTTPS endpoint supplied by GIA')
        form.addRow('GIA endpoint', url)
    key = QLineEdit()
    key.setEchoMode(QLineEdit.Password)
    if existing.get('key'):
        key.setPlaceholderText('API credential saved')
    form.addRow('API key' if lab == 'GIA' else 'API token', key)
    buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    layout.addWidget(buttons)

    def save():
        try:
            credentials = {}
            if lab == 'GIA':
                credentials['url'] = gia_endpoint(url.text())
            credentials['key'] = key.text().strip()
            if not credentials['key'] and credentials.get('url') == existing.get('url'):
                credentials['key'] = existing.get('key', '')
            if not credentials['key']:
                raise ReportLookupError(f'Enter your {lab} API credential.')
            portal_secrets.put_secret(f'lab-report-{lab.lower()}', json.dumps(credentials))
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
    def __init__(self, lab, number, credentials, parent):
        super().__init__(parent)
        self.lab = lab
        self.number = number
        self.credentials = credentials
        self.result = None
        self.error = ''

    def run(self):
        try:
            self.result = lookup_report(self.lab, self.number, self.credentials)
        except ReportLookupError as error:
            self.error = str(error)
        except Exception:
            self.error = 'The lab returned an unexpected response.'
        finally:
            self.credentials = {}


class InventoryRowDialog(QDialog):
    def __init__(self, parent, profile, row_number, stamp, columns, stock_ids):
        super().__init__(parent)
        self.profile = profile
        self.row_number = row_number
        self.stamp = stamp
        self.columns = columns
        self.stock_ids = stock_ids
        self.request = None
        self.setWindowTitle('Add diamond' if row_number is None else f'Edit Excel row {row_number}')
        self.resize(680, 720)
        layout = dialog_layout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.lab = QComboBox()
        self.lab.setEditable(True)
        self.lab.addItems(['', *LABS])
        self.number = QLineEdit()
        self.number.setPlaceholderText('Report number')
        form.addRow('Lab', self.lab)
        form.addRow('Report number', self.number)
        lookup_row = QHBoxLayout()
        self.lookup_button = QPushButton('Look up report')
        self.settings_button = QPushButton('Lookup settings')
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
            if column.field == 'Lab':
                self.lab.setCurrentText(column.text)
            elif column.field == 'Certificate #':
                self.number.setText(column.text)
            else:
                entry = QLineEdit(column.text)
                entry.setReadOnly(column.formula)
                label = f'{column.header} (%)' if column.percent and '%' not in column.header else column.header
                fields.addRow(label, entry)
                self.inputs[column.index] = entry
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.worksheet_fields = {column.field for column in columns}
        if not {'Lab', 'Certificate #'} <= self.worksheet_fields:
            self.status.setText('This worksheet needs Lab and Report # columns to save a certificate.')
            self.lookup_button.setEnabled(False)
        if any(column.formula for column in columns):
            formula_note = QLabel('Open and save the workbook in Excel to recalculate formulas after editing their inputs.')
            formula_note.setWordWrap(True)
            layout.addWidget(formula_note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.Save)
        self.save_button.setText('Save to Excel')
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.lookup_button.clicked.connect(self.begin_lookup)
        self.settings_button.clicked.connect(self.open_settings)
        self.lab.currentTextChanged.connect(self.update_lookup_label)
        self.update_lookup_label()

    def update_lookup_label(self):
        lab = self.lab.currentText().strip().upper()
        self.lookup_button.setText('Open IGI report' if lab == 'IGI' else 'Look up report')
        self.settings_button.setEnabled(lab in ('GIA', 'HRD') and self.request is None)

    def open_settings(self):
        lab = self.lab.currentText().strip().upper()
        if lab in ('GIA', 'HRD'):
            _lookup_settings(self, lab)

    def begin_lookup(self):
        lab = self.lab.currentText().strip().upper()
        try:
            if lab not in LABS:
                raise ReportLookupError('Choose GIA, IGI, or HRD to look up a certificate.')
            number = report_number(self.number.text())
            if lab == 'IGI':
                if not QDesktopServices.openUrl(QUrl(report_check_url(lab, number))):
                    raise ReportLookupError('Could not open the IGI verification page.')
                self.status.setText('Enter the grades from the IGI report in the fields below.')
                return
            credentials = _credentials(lab)
            if not credentials.get('key'):
                raise ReportLookupError(f'Add your {lab} API credential under Lookup settings first.')
        except ReportLookupError as error:
            self.status.setText(str(error))
            return
        except Exception:
            self.status.setText('Could not read the API credential from Windows Credential Manager.')
            return
        self.request = _ReportRequest(lab, number, credentials, self)
        self.request.finished.connect(self.finish_lookup)
        self.status.setText(f'Looking up the {lab} report…')
        for widget in (self.lab, self.number, self.lookup_button, self.settings_button, self.save_button):
            widget.setEnabled(False)
        self.request.start()

    @Slot()
    def finish_lookup(self):
        request = self.request
        self.request = None
        for widget in (self.lab, self.number, self.lookup_button, self.save_button):
            widget.setEnabled(True)
        self.update_lookup_label()
        if request.error:
            self.status.setText(request.error)
        else:
            kind, details = request.result
            if kind and kind != self.profile['kind']:
                self.status.setText(f'The report is for a {kind} diamond. Open the {kind} workbook to add it.')
            else:
                for column in self.columns:
                    entry = self.inputs.get(column.index)
                    if entry is not None and not column.formula and column.field in details:
                        entry.setText(details[column.field])
                self.status.setText('Review the returned grades and fill in any missing information before saving.')
        request.deleteLater()

    def reject(self):
        if self.request is not None:
            self.status.setText('Wait for the lookup to finish before closing the form.')
            return
        super().reject()

    def save(self):
        lab = self.lab.currentText().strip()
        number = self.number.text().strip()
        try:
            if lab or number:
                if not {'Lab', 'Certificate #'} <= self.worksheet_fields:
                    raise ReportLookupError('Add Lab and Report # columns to the worksheet before saving a certificate.')
                if not lab:
                    raise ReportLookupError('Enter the lab for this report.')
                number = report_number(number)
            text = {}
            for column in self.columns:
                if column.field == 'Lab':
                    text[column.index] = lab.upper() if lab.upper() in LABS else lab
                elif column.field == 'Certificate #':
                    text[column.index] = number
                else:
                    text[column.index] = self.inputs[column.index].text()
            values = row_values(self.columns, text, self.stock_ids, adding=self.row_number is None)
            save_inventory_row(self.profile, self.row_number, values, self.stamp)
        except ReportLookupError as error:
            self.status.setText(str(error))
        except Exception as error:
            self.status.setText(workbook_error(self.profile, error))
        else:
            self.accept()


def edit_inventory(parent, profile, row_number=None, expected_stamp=None):
    try:
        stamp, columns, stock_ids = read_inventory_row(profile, row_number, expected_stamp)
        dialog = InventoryRowDialog(parent, profile, row_number, stamp, columns, stock_ids)
        return dialog.exec() == QDialog.Accepted
    except Exception as error:
        QMessageBox.warning(parent, 'Could not open Excel editor', workbook_error(profile, error))
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

