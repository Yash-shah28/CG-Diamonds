import os

import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')

pytest.importorskip('watchdog')

pytest.importorskip('keyring')

from PySide6.QtWidgets import QApplication

from drc_sync.app import Window, Worker, Events

from drc_sync.core import Store

@pytest.fixture
def profile(tmp_path):
    from openpyxl import Workbook
    path = tmp_path/'stock.xlsx'
    wb = Workbook(); ws = wb.active; ws.title = 'Stock'
    ws.append(['Stock No', 'Weight']); ws.append(['A1', 1.25]); wb.save(path)
    return {'id':'natural', 'kind':'Natural', 'path':str(path), 'sheet':'Stock',
            'mapping':{'Stock No':'stock_id', 'Weight':'carat'},
            'required':['stock_id'], 'numeric':['carat'], 'identity':'stock_id'}


def test_events_atomic_replace_and_temporary_files(tmp_path):
    from watchdog.events import FileMovedEvent, FileModifiedEvent
    paths = []
    events = Events(paths.append)
    events.on_any_event(FileModifiedEvent(str(tmp_path/'~$temp.xlsx')))
    assert paths == []
    events.on_any_event(FileMovedEvent(str(tmp_path/'temporary'), str(tmp_path/'stock.xlsx')))
    assert str((tmp_path/'stock.xlsx').resolve()) in paths


def test_second_launch_restores_existing_window(tmp_path, monkeypatch):
    from drc_sync.instance import Instance
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    primary = Instance(tmp_path)
    secondary = Instance(tmp_path)
    assert primary.acquire()
    assert not secondary.acquire()
    window = Window(tmp_path/'activation.db')
    primary.activated.connect(window.open_window)
    window.hide()
    assert secondary.notify()
    deadline = time.monotonic() + 2
    while not window.isVisible() and time.monotonic() < deadline:
        app.processEvents()
    assert window.isVisible()
    window.tray.hide(); window.timer.stop(); window.store.db.close()
    primary.close()
    assert secondary.acquire()
    secondary.close()
    window.deleteLater(); app.processEvents()


def test_auto_map_existing_profile_preserves_custom_mapping(tmp_path, monkeypatch):
    from openpyxl import Workbook
    from drc_sync.contract import new_profile
    import json
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    p=new_profile(); p['path']=str(tmp_path/'mapping.xlsx'); p['sheet']='Stock'
    p['mapping']={'Private Header':'CustomAcceptedField'}
    wb=Workbook(); ws=wb.active; ws.title='Stock'; ws.append(['Private Header','Stock #','PPC','Report No','Photo 6','Unknown Column']); wb.save(p['path'])
    window=Window(tmp_path/'mapping.db')
    window.editor.setPlainText(json.dumps([p])); window.auto_map()
    edited=json.loads(window.editor.toPlainText())[0]
    assert edited['mapping']=={'Private Header':'CustomAcceptedField','Stock #':'StockId','PPC':'PricePerCarat','Report No':'Certificate #','Photo 6':'Image 6'}
    assert window.store.get('profiles',[])==[]
    window.tray.hide(); window.timer.stop(); window.store.db.close()
    window.deleteLater(); app.processEvents()


@pytest.mark.parametrize('initial', ['', '{}', '[]'])
def test_add_excel_from_empty_settings(tmp_path, monkeypatch, initial):
    from PySide6.QtWidgets import QFileDialog,QInputDialog,QDialog,QMessageBox
    from openpyxl import Workbook
    import json
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker,'start',lambda self:None)
    path=tmp_path/'rapnet.xlsx'
    wb=Workbook(); ws=wb.active; ws.title='Rapnet'
    ws.append(['Stock #','Shape','Weight','Color','Clarity','Price Per Ct','Report #','Cert comment','VideoURL'])
    ws.append(['A1','Round',1.01,'D','VVS1',5000,'123','Comment','https://example.com/video'])
    wb.save(path)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a:(str(path),''))
    monkeypatch.setattr('drc_sync.app.choose_item',lambda *a:('Rapnet',True))
    monkeypatch.setattr(QDialog,'exec',lambda self:QDialog.Accepted)
    warnings=[]; monkeypatch.setattr(QMessageBox,'warning',lambda *a:warnings.append(a[1:]))
    w=Window(tmp_path/'add.db'); w.editor.setPlainText(initial); w.add_file()
    assert not warnings
    p=json.loads(w.editor.toPlainText())[0]
    assert p['mapping']['Price Per Ct']=='PricePerCarat'
    assert p['mapping']['Report #']=='Certificate #'
    assert p['mapping']['VideoURL']=='Video'
    assert p['mapping']['Cert comment']=='Certificate Comment'
    assert w.store.get('profiles',[])==[]
    w.tray.hide(); w.timer.stop(); w.store.db.close(); w.deleteLater(); app.processEvents()


def test_workbook_error_does_not_claim_invalid_json(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QFileDialog,QMessageBox
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker,'start',lambda self:None)
    path=tmp_path/'broken.xlsx'; path.write_text('not an Excel file')
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a:(str(path),''))
    warnings=[]; monkeypatch.setattr(QMessageBox,'warning',lambda *a:warnings.append(a[1:]))
    w=Window(tmp_path/'bad.db'); w.add_file()
    assert warnings[0][0]=='Could not add workbook'
    assert 'JSON' not in warnings[0][1]
    w.editor.setPlainText('{broken')
    with pytest.raises(ValueError,match='line 1'): w.read_profiles()
    w.reload_profiles(); assert w.read_profiles()==[]
    w.tray.hide(); w.timer.stop(); w.store.db.close(); w.deleteLater(); app.processEvents()


def test_response_viewer_uses_plain_text_and_preserved_attempts(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QDialog,QPlainTextEdit,QComboBox
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker,'start',lambda self:None)
    window=Window(tmp_path/'view.db')
    n=window.store.enqueue('natural',{'diamonds':[{'StockId':'A1'}]})
    job=window.store.db.execute('SELECT * FROM jobs').fetchone()
    window.store.record_response(job,{'path':'file.xlsx','kind':'Natural'},'secret',200,120,b'{"message":"hello"}',False,'','review')
    captured=[]
    def inspect(dialog):
        captured.append(dialog.findChild(QPlainTextEdit).toPlainText())
        assert dialog.findChild(QComboBox).count()==1
        return QDialog.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect)
    window.show_response(n)
    assert 'HTTP status: 200' in captured[0] and '120 ms' in captured[0] and 'Needs review' in captured[0]
    assert 'hello' in captured[0]
    window.tray.hide(); window.timer.stop(); window.store.db.close()
    window.deleteLater(); app.processEvents()


def test_history_failure_snapshot_can_be_viewed_without_database(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QDialog,QPlainTextEdit
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker,'start',lambda self:None)
    window=Window(tmp_path/'snapshot.db')
    window.store.db.close()
    result={'saved':False,'job':{'id':9,'manual':1,'state':'review','detail':''},'response':{
        'attempt':1,'status':200,'elapsed_ms':100,'body':'{"uploadId":1671}','error':'Failure stage: history saving\nException type: OSError',
        'state':'review','created':time.time(),'endpoint':'https://connect.drc.diamonds/v1/inventory/push','filename':'file.xlsx','kind':'Natural','truncated':0}}
    text=[]
    monkeypatch.setattr(QDialog,'exec',lambda d:(text.append(d.findChild(QPlainTextEdit).toPlainText()) or QDialog.Rejected))
    window.inspection_problem(result)
    assert '1671' in text[0] and 'history saving' in text[0] and 'HTTP status: 200' in text[0]
    window.tray.hide(); window.timer.stop(); window.deleteLater(); app.processEvents()


def test_fixed_type_connection_replaces_only_that_type(tmp_path, monkeypatch):
    import json
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QDialog, QMessageBox
    from drc_sync.contract import new_profile
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    from openpyxl import Workbook
    old = tmp_path/'old.xlsx'; replacement = tmp_path/'new.xlsx'; labfile = tmp_path/'lab.xlsx'
    for path in (old, replacement, labfile):
        wb = Workbook(); wb.active.title = 'Stock'; wb.active.append(['Stock #', 'Shape', 'Weight', 'Price Per Ct']); wb.save(path)
    natural = new_profile(); natural.update(id='natural', path=str(old), sheet='Stock', confirmed=True, success_field='success')
    lab = new_profile(); lab.update(id='lab', kind='Lab Grown', path=str(labfile), sheet='Stock')
    window = Window(tmp_path/'replace.db'); window.store.set('profiles', [natural, lab]); window.reload_profiles()
    job = window.store.enqueue('natural', {'diamonds': []})
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(replacement), ''))
    monkeypatch.setattr('drc_sync.app.choose_item', lambda *a: ('Stock', True))
    monkeypatch.setattr(QDialog, 'exec', lambda self: QDialog.Accepted)
    warnings = []; monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a))
    window.add_file('Natural', persist=True)
    profiles = window.store.get('profiles', [])
    assert not warnings
    assert len(profiles) == 2
    assert next(p for p in profiles if p['kind'] == 'Lab Grown') == lab
    saved = next(p for p in profiles if p['kind'] == 'Natural')
    assert saved['path'] == str(replacement) and saved['id'] == 'natural'
    assert saved['confirmed'] and saved['success_field'] == 'success'
    assert window.store.db.execute('SELECT filename FROM jobs WHERE id=?', (job,)).fetchone()[0] == str(old)
    assert window.worker.requests.empty()
    assert window.page_names == ['Dashboard', 'Excel Mapping', 'API Access', 'Change Rates', 'History']
    window.tray.hide(); window.timer.stop(); window.store.db.close(); window.deleteLater(); app.processEvents()


@pytest.mark.parametrize('inventory_kind', ['Natural', 'Lab Grown'])
def test_edit_mapping_fills_missing_recognized_columns(tmp_path, monkeypatch, inventory_kind):
    import json
    from openpyxl import Workbook
    from PySide6.QtWidgets import QInputDialog, QDialog, QMessageBox
    from drc_sync.contract import new_profile
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    path = tmp_path/'partial.xlsx'
    wb = Workbook(); wb.active.title = 'Stock'
    wb.active.append(['Stock #', 'Availability', 'Shape', 'Cut Grade', 'Polish', 'Symmetry', 'Custom Column', 'Weight'])
    wb.save(path)
    p = new_profile(); p.update(kind=inventory_kind, path=str(path), sheet='Stock', mapping={'Shape':'Shape','Availability':'','Custom Column':'CustomAcceptedField'})
    window = Window(tmp_path/'partial.db'); window.editor.setPlainText(json.dumps([p]))
    monkeypatch.setattr('drc_sync.app.choose_item', lambda *a: ('Stock', True))
    monkeypatch.setattr(QDialog, 'exec', lambda self: QDialog.Accepted)
    warnings = []; monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a))
    window.add_file(inventory_kind, edit=True)
    assert not warnings
    mapping = window.read_profiles()[0]['mapping']
    assert mapping == {'Stock #':'StockId', 'Availability':'Status', 'Shape':'Shape', 'Cut Grade':'Cut', 'Polish':'Polish', 'Symmetry':'Symmetry', 'Custom Column':'CustomAcceptedField', 'Weight':'Weight'}
    assert window.worker.requests.empty()
    window.tray.hide(); window.timer.stop(); window.store.db.close(); window.deleteLater(); app.processEvents()


def test_history_filter_and_selected_response(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    window=Window(tmp_path/'history-filter.db')
    ids=[]
    for state in ('sent','failed'):
        job_id=window.store.enqueue('natural',{'diamonds':[{'StockId':state}]},{'kind':'Natural','path':'stock.xlsx'},force=True)
        job=window.store.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone();window.store.finish(job,state,'Mocked outcome');ids.append(job_id)
    window.refresh();window.navigate(3);window.history_filter.setCurrentText('Failure')
    assert window.history_ids==[ids[1]] and window.history.rowCount()==1
    viewed=[];monkeypatch.setattr(window,'show_response',lambda job_id:viewed.append(job_id));window.history.selectRow(0);window.open_history_response(0)
    assert viewed==[ids[1]]
    window.tray.hide();window.timer.stop();window.store.db.close();window.deleteLater();app.processEvents()


def test_sheet_picker_has_readable_spacing_and_secondary_cancel(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QDialog,QComboBox,QDialogButtonBox
    from drc_sync.dialogs import choose_item
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    w=Window(tmp_path/'picker-layout.db');captured=[]
    def inspect(dialog):
        assert dialog.minimumWidth()>=440
        combo=dialog.findChild(QComboBox);assert combo.minimumHeight()>=44 and combo.currentText()=='Inventory'
        buttons=dialog.findChild(QDialogButtonBox);assert buttons.button(QDialogButtonBox.Cancel).objectName()=='quiet'
        captured.append(True);return QDialog.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect)
    assert choose_item(w,'Worksheet','Choose inventory sheet',['Inventory'])==('Inventory',False)
    assert captured
    w.tray.hide();w.timer.stop();w.store.db.close();w.deleteLater();app.processEvents()


def test_mapping_popup_readable_editors_and_grid(tmp_path,monkeypatch):
    from openpyxl import Workbook
    from PySide6.QtWidgets import QDialog,QFileDialog,QTableWidget,QComboBox,QDialogButtonBox
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    path=tmp_path/'columns.xlsx';wb=Workbook();wb.active.title='Inventory';wb.active.append(['Stock #','Weight','Unknown']);wb.save(path)
    w=Window(tmp_path/'mapping-layout.db')
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a:(str(path),''));monkeypatch.setattr('drc_sync.app.choose_item',lambda *a:('Inventory',True))
    def inspect(dialog):
        table=dialog.findChild(QTableWidget);assert table.showGrid() and table.verticalHeader().isHidden()
        assert table.rowHeight(0)>=56
        assert table.cellWidget(0,1).currentText()=='StockId'
        combo=table.cellWidget(0,1).findChild(QComboBox);assert combo.minimumHeight()>=40
        dialog.show();app.processEvents()
        assert combo.geometry().bottom()<table.cellWidget(0,1).height()
        assert combo.lineEdit().height()>=combo.lineEdit().fontMetrics().height()
        dialog.hide()
        assert table.cellWidget(2,1).findChild(QComboBox).lineEdit().placeholderText()=='Skip column'
        assert dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel).objectName()=='quiet'
        return QDialog.Rejected
    monkeypatch.setattr(QDialog,'exec',inspect);w.add_file('Natural')
    assert w.store.get('profiles',[])==[]
    w.tray.hide();w.timer.stop();w.store.db.close();w.deleteLater();app.processEvents()
