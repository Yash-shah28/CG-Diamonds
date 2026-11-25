import os
from pathlib import Path
from xml.dom import minidom
from zipfile import ZipFile

import httpx
import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.table import Table

from drc_sync.core import WorkbookValidationError
from drc_sync.contract import new_profile
from drc_sync.inventory_excel import download_inventory, read_inventory_row, row_values, save_inventory_row
from drc_sync.lab_reports import lookup_report

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture
def profile(tmp_path):
    path = tmp_path/'stock.xlsx'
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Stock'
    sheet.append(['Stock #', 'Shape', 'Weight', 'Price Per Carat', 'Lab', 'Report #', 'Depth %', 'Total Price'])
    sheet.append(['A1', 'Round', 1.0, 100, 'IGI', '000001', 0.619, '=C2*D2'])
    sheet['G2'].number_format = '0.00%'
    sheet.add_table(Table(displayName='StockTable', ref='A1:H2'))
    workbook.create_sheet('Notes')['A1'] = 'Leave this sheet intact'
    workbook.save(path)
    # openpyxl does not write a cached formula value, so seed one as Excel would.
    with ZipFile(path) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    part = 'xl/worksheets/sheet1.xml'
    doc = minidom.parseString(entries[part])
    formula, = (cell for cell in doc.getElementsByTagName('c') if cell.getAttribute('r') == 'H2')
    cached, = formula.getElementsByTagName('v')
    cached.appendChild(doc.createTextNode('100'))
    entries[part] = doc.toxml(encoding='utf-8')
    with ZipFile(path, 'w') as rebuilt:
        for name, content in entries.items():
            rebuilt.writestr(name, content)
    connection = new_profile()
    connection.update(path=str(path), sheet='Stock', mapping={
        'Stock #': 'StockId', 'Shape': 'Shape', 'Weight': 'Weight', 'Price Per Carat': 'PricePerCarat',
        'Lab': 'Lab', 'Report #': 'Certificate #', 'Depth %': 'Depth %', 'Total Price': 'Final Price',
    })
    return connection


def _values(columns, stocks, changes, adding):
    text = {column.index: changes.get(column.field, column.text) for column in columns}
    return row_values(columns, text, stocks, adding=adding)


def test_excel_save_preserves_formula_cache_and_adds_a_styled_row(profile, tmp_path):
    stamp, columns, stocks = read_inventory_row(profile, 2)
    assert next(c for c in columns if c.field == 'Depth %').text == '61.9'
    assert next(c for c in columns if c.field == 'Final Price').formula
    save_inventory_row(profile, 2, _values(columns, stocks, {'Shape': 'Oval'}, False), stamp)
    stamp, columns, stocks = read_inventory_row(profile)
    save_inventory_row(profile, None, _values(columns, stocks, {
        'StockId': '0002', 'Shape': 'Round', 'Weight': '1.2', 'PricePerCarat': '500',
        'Lab': 'HRD', 'Certificate #': '000014', 'Depth %': '59.50',
    }, True), stamp)

    source = load_workbook(profile['path'], data_only=True)
    try:
        sheet = source['Stock']
        assert sheet['B2'].value == 'Oval'
        assert sheet['H2'].value == 100
        assert sheet['A3'].value == '0002' and sheet['F3'].value == '000014'
        assert sheet['G3'].value == 0.595 and sheet['G3'].number_format == '0.00%'
        assert sheet.tables['StockTable'].ref == 'A1:H3'
        assert source['Notes']['A1'].value == 'Leave this sheet intact'
    finally:
        source.close()
    copy = tmp_path/'export.xlsx'
    download_inventory(profile, copy)
    with open(profile['path'], 'rb') as original:
        assert copy.read_bytes() == original.read()


def test_edit_rejects_stale_file_and_duplicate_stock(profile):
    stamp, columns, stocks = read_inventory_row(profile)
    with pytest.raises(WorkbookValidationError, match='already in'):
        _values(columns, stocks, {'StockId': 'A1', 'Shape': 'Oval', 'Weight': '1', 'PricePerCarat': '1'}, True)
    save_inventory_row(profile, 2, {2: 'Oval'}, stamp)
    with pytest.raises(WorkbookValidationError, match='Excel changed'):
        save_inventory_row(profile, 2, {2: 'Pear'}, stamp)
    with pytest.raises(WorkbookValidationError, match='Excel changed'):
        read_inventory_row(profile, 2, stamp)


def test_gia_and_hrd_use_documented_format():
    def provider(request):
        if request.url.host == 'gia.example':
            assert request.method == 'POST' and request.headers['Authorization'] == 'placeholder'
            assert b'3435445806' in request.content
            return httpx.Response(200, json={'data': {'getReport': {
                'report_number': '3435445806',
                'results': {'__typename': 'DiamondGradingReportResults', 'carat_weight': '1.21 carat',
                            'shape_and_cutting_style': 'Round Brilliant', 'proportions': {'depth_pct': '61.90%'}},
            }}})
        assert request.url.path.endswith('/GetReport/250000160172')
        assert request.headers['API-Token'] == 'placeholder'
        return httpx.Response(200, content=b'''<Report><ReportNumber>250000160172</ReportNumber>
            <Results><Shape><ShapeCode>brilliant</ShapeCode></Shape><Carat>0.93</Carat></Results>
            <AdditionalInformation><Origin>Natural</Origin><TableWidth><TableWidthPercent>62</TableWidthPercent>
            </TableWidth></AdditionalInformation><Remarks><Remark>First</Remark><Remark>Second</Remark></Remarks>
            <Inscriptions><Inscription>HRD 250000160172</Inscription></Inscriptions></Report>''')

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        assert lookup_report('GIA', '3435445806', {'url': 'https://gia.example/report', 'key': 'placeholder'}, client=client) == (
            'Natural', {'Shape': 'Round', 'Weight': '1.21', 'Depth %': '61.90'})
        assert lookup_report('HRD', '250000160172', {'key': 'placeholder'}, client=client) == (
            'Natural', {'Shape': 'Round', 'Weight': '0.93', 'Table': '62',
                        'Certificate Comment': 'First; Second', 'Laser Inscription': 'HRD 250000160172'})


@pytest.mark.parametrize('kind', ['Natural', 'Lab Grown'])
def test_inventory_window_can_import_hrd_and_download(profile, kind, tmp_path, monkeypatch):
    from PySide6.QtCore import QEventLoop, Qt, QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QPushButton, QTableView, QWidget

    from drc_sync.dashboard import show_workbook_inventory
    from drc_sync import inventory_edit_ui

    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    exported = tmp_path/'downloaded.xlsx'
    profile['kind'] = kind
    dialogs = []

    def choose_destination(parent, title, filename, filters):
        assert filename == f'{kind} Inventory.xlsx'
        return str(exported), filters

    def lookup(lab, number, credentials):
        assert (lab, number, credentials) == ('HRD', '250000160172', {'key': 'test-token'})
        return kind, {'Shape': 'Round', 'Weight': '0.93', 'Depth %': '61.4'}

    monkeypatch.setattr(inventory_edit_ui, '_credentials', lambda: {'key': 'test-token'})
    monkeypatch.setattr(inventory_edit_ui, 'lookup_report', lookup)

    def exercise(dialog):
        if isinstance(dialog, inventory_edit_ui.HrdImportDialog):
            assert not dialog.save_button.isEnabled()
            dialog.number.setText('250000160172')
            dialog.lookup_button.click()
            assert dialog.request is not None
            event_loop = QEventLoop()
            dialog.request.finished.connect(event_loop.quit)
            QTimer.singleShot(3000, event_loop.quit)
            event_loop.exec()
            assert dialog.save_button.isEnabled()
            for column in dialog.columns:
                if column.field == 'StockId':
                    dialog.inputs[column.index].setText('HRD-1')
                elif column.field == 'PricePerCarat':
                    dialog.inputs[column.index].setText('210')
            dialog.save_button.click()
            assert dialog.result() == QDialog.Accepted, dialog.status.text()
            return QDialog.Accepted
        dialogs.append(dialog.windowTitle())
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        assert set(buttons) == {'Import HRD report', 'Download Excel'}
        table = dialog.findChild(QTableView)
        assert table.model().rowCount() == 1
        index = table.model().index(0, 0)
        assert not table.model().flags(index) & Qt.ItemIsEditable
        buttons['Import HRD report'].click()
        assert table.model().rowCount() == 2
        assert table.model().data(table.model().index(1, 0), Qt.DisplayRole) == 'HRD-1'
        buttons['Download Excel'].click()
        return QDialog.Rejected

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose_destination)
    monkeypatch.setattr(QDialog, 'exec', exercise)
    show_workbook_inventory(owner, profile)
    assert dialogs == [f'{kind} Diamonds']
    assert exported.read_bytes() == Path(profile['path']).read_bytes()
    workbook = load_workbook(profile['path'], data_only=True)
    try:
        sheet = workbook['Stock']
        assert sheet['E3'].value == 'HRD'
        assert sheet['F3'].value == '250000160172'
        assert sheet['G3'].value == 0.614
    finally:
        workbook.close()
    owner.deleteLater()
    app.processEvents()
