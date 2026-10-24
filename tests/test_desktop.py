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


def test_window_smoke(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    window = Window(tmp_path/'ui.db')
    assert window.pages.count() == 4
    assert window.windowTitle() == 'Chintamani Gems'
    assert not window.windowIcon().isNull()
    window.pause()
    assert window.worker.paused
    window.pause()
    window.tray.hide()
    window.timer.stop()
    window.store.db.close()
    window.deleteLater()
    app.processEvents()


def test_events_atomic_replace_and_temporary_files(tmp_path):
    from watchdog.events import FileMovedEvent, FileModifiedEvent
    paths = []
    events = Events(paths.append)
    events.on_any_event(FileModifiedEvent(str(tmp_path/'~$temp.xlsx')))
    assert paths == []
    events.on_any_event(FileMovedEvent(str(tmp_path/'temporary'), str(tmp_path/'stock.xlsx')))
    assert str((tmp_path/'stock.xlsx').resolve()) in paths


def test_worker_debounce_and_file_change(tmp_path, profile, monkeypatch):
    from openpyxl import load_workbook
    import drc_sync.app as module
    profile['confirmed'] = False
    dbpath = tmp_path/'worker.db'
    store = Store(dbpath); store.set('profiles', [profile])
    monkeypatch.setattr(module, 'read_key', lambda kind: 'mock-key')
    worker = Worker(dbpath)
    worker.start()
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]: break
            time.sleep(.1)
        assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
        time.sleep(3)
        assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
        wb = load_workbook(profile['path']); wb['Stock']['B2'] = 2.5; wb.save(profile['path']); wb.close()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 2: break
            time.sleep(.1)
        assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 2
    finally:
        worker.requestInterruption(); worker.wait(5000)
        assert not worker.isRunning()
        store.db.close()

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

@pytest.mark.parametrize('accepted',[False,True],ids=['cancel','explicit-send'])
def test_manual_confirmation_controls_dispatch(tmp_path,monkeypatch,accepted):
    from PySide6.QtWidgets import QDialog
    from drc_sync.contract import new_profile
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker,'start',lambda self:None)
    window=Window(tmp_path/'confirm.db')
    profile=new_profile(); profile['path']='inventory.xlsx'
    payload={'diamonds':[{'StockId':'A1'}]}
    calls=[]
    monkeypatch.setattr(window.worker,'send_inspection',lambda p,data:calls.append((p,data)))
    monkeypatch.setattr(QDialog,'exec',lambda self: QDialog.Accepted if accepted else QDialog.Rejected)
    window.inspect_button.setEnabled(False)
    window.confirm_inspection(profile,payload)
    assert len(calls)==int(accepted)
    if not accepted: assert window.inspect_button.isEnabled()
    assert window.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
    window.tray.hide(); window.timer.stop(); window.store.db.close()
    window.deleteLater(); app.processEvents()


def test_prepare_has_no_http_and_manual_worker_sends_once(tmp_path,profile,monkeypatch):
    import httpx
    import drc_sync.app as module
    from drc_sync.core import deliver as real_deliver
    from drc_sync.contract import ENDPOINT
    profile['endpoint']=ENDPOINT; profile['confirmed']=False; profile['retry_safe']=True
    store=Store(tmp_path/'worker-response.db'); worker=Worker(tmp_path/'worker-response.db')
    prepared=[]; completed=[]
    worker.prepared.connect(lambda p,data: prepared.append((p,data)))
    worker.response_ready.connect(completed.append)
    def no_key(kind): raise AssertionError('Preparing must not access credentials')
    monkeypatch.setattr(module,'read_key',no_key)
    worker.process_inspection(store,('prepare',profile,None))
    assert len(prepared)==1 and not completed
    assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'received':1})
    monkeypatch.setattr(module,'read_key',lambda kind:'mock-key')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(module,'deliver',lambda *args,**kwargs:real_deliver(*args,client=client,**kwargs))
        worker.process_inspection(store,('send',*prepared[0]))
    assert len(calls)==1 and len(completed)==1
    job=store.db.execute('SELECT * FROM jobs').fetchone()
    assert job['manual']==1 and job['state']=='review'
    assert store.db.execute('SELECT COUNT(*) FROM responses').fetchone()[0]==1
    assert store.db.execute("SELECT COUNT(*) FROM jobs WHERE manual=0 AND state IN ('pending','retry')").fetchone()[0]==0
    store.db.close()


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

def test_credential_failure_is_blocked_before_dispatch(tmp_path,monkeypatch):
    import drc_sync.app as module
    from drc_sync.contract import new_profile
    store=Store(tmp_path/'credential-failure.db'); worker=Worker(tmp_path/'credential-failure.db')
    def fail(kind): raise RuntimeError('SECRET-VALUE')
    monkeypatch.setattr(module,'read_key',fail)
    worker.process_inspection(store,('send',new_profile(),{'diamonds':[{'StockId':'A1'}]}))
    row=store.db.execute('SELECT * FROM responses').fetchone()
    assert row['state']=='blocked' and row['status'] is None
    assert 'credential lookup' in row['error'] and 'RuntimeError' in row['error']
    assert 'No request sent' in row['error'] and 'SECRET-VALUE' not in row['error']
    store.db.close()


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

def test_manual_job_creation_failure_does_not_dispatch(tmp_path,monkeypatch):
    import drc_sync.app as module
    from drc_sync.contract import new_profile
    store=Store(tmp_path/'creation-failure.db'); worker=Worker(tmp_path/'creation-failure.db')
    def fail(*a): raise OSError('SECRET-VALUE')
    monkeypatch.setattr(store,'enqueue_manual',fail)
    monkeypatch.setattr(module,'read_key',lambda k: (_ for _ in ()).throw(AssertionError('Must not read credentials')))
    results=[]; worker.inspection_problem.connect(results.append)
    worker.process_inspection(store,('send',new_profile(),{'diamonds':[{'StockId':'A1'}]}))
    assert len(results)==1
    assert results[0]['job']['state']=='blocked'
    assert 'No request sent' in results[0]['response']['error']
    assert 'SECRET-VALUE' not in str(results)
    store.db.close()

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
    assert window.page_names == ['Dashboard', 'Excel Mapping', 'Change Rates', 'History']
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

def test_save_monitor_sends_once_per_save_even_unchanged(tmp_path, profile, monkeypatch):
    import httpx
    from openpyxl import load_workbook
    import drc_sync.app as module
    from drc_sync.core import deliver as real_deliver
    profile.update(upload_on_save=True, confirmed=False, success_field='')
    dbpath=tmp_path/'save-monitor.db'; store=Store(dbpath); store.set('profiles',[profile])
    calls=[]; kinds=[]
    monkeypatch.setattr(module,'read_key',lambda kind:kinds.append(kind) or 'offline-key')
    client=httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200,json={'success':True,'message':'Push queued successfully','uploadId':123})))
    monkeypatch.setattr(module,'deliver',lambda s,j,p,k:real_deliver(s,j,p,k,client=client))
    worker=Worker(dbpath); worker.start()
    try:
        time.sleep(3)
        assert not calls
        for expected in (1,2):
            wb=load_workbook(profile['path']); wb.save(profile['path']); wb.close()
            deadline=time.monotonic()+9
            while len(calls)<expected and time.monotonic()<deadline: time.sleep(.1)
            assert len(calls)==expected
            time.sleep(3)
            assert len(calls)==expected
        assert kinds==['Natural','Natural']
        assert store.db.execute("SELECT COUNT(*) FROM jobs WHERE state='sent'").fetchone()[0]==2
    finally:
        worker.requestInterruption(); worker.wait(); store.db.close(); client.close()


def test_requested_controls_removed_and_sent_notification(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QPushButton, QSystemTrayIcon
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    notices=[]
    monkeypatch.setattr(QSystemTrayIcon, 'showMessage', lambda self,*args: notices.append(args))
    window=Window(tmp_path/'controls.db')
    labels=[b.text() for i in range(window.pages.count()) for b in window.pages.widget(i).findChildren(QPushButton)]
    assert not any(label in labels for label in ('Sync Now','Refresh','Send once & view response','API connection','Check local setup'))
    window.worker.sent_to_drc.emit('Lab Grown')
    assert len(notices)==1
    assert notices[0][0]=='Inventory sent to DRC'
    assert 'Lab Grown' in notices[0][1] and 'successfully sent' in notices[0][1]
    window.tray.hide();window.timer.stop();window.store.db.close();window.deleteLater();app.processEvents()


def test_drc_rate_tab_saves_and_restores_without_upload(tmp_path, monkeypatch):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker,'start',lambda self:None)
    window=Window(tmp_path/'rates.db')
    assert window.page_names[2]=='Change Rates'
    window.rate_mode.setCurrentIndex(1);window.rate_percent.setValue(10);window.save_rate()
    assert window.store.get('website_rates',{})=={'drc':{'mode':'increase','percent':'10.0'}}
    assert window.worker.requests.empty() and not window.worker.manual
    assert window.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
    assert window.rate_table.columnCount()==5
    assert all(window.rate_table.horizontalHeaderItem(i).text()!='Price example' for i in range(5))
    window.tray.hide();window.timer.stop();window.store.db.close();window.deleteLater();app.processEvents()
    restored=Window(tmp_path/'rates.db')
    assert restored.rate_mode.currentText()=='Increase' and restored.rate_percent.value()==10
    restored.tray.hide();restored.timer.stop();restored.store.db.close();restored.deleteLater();app.processEvents()


@pytest.mark.parametrize('kind',['Natural','Lab Grown'])
def test_prepared_snapshot_uses_saved_website_rate(tmp_path, monkeypatch, kind):
    import drc_sync.app as module
    store=Store(tmp_path/'rate-snapshot.db');store.set('website_rates',{'drc':{'mode':'discount','percent':'5'}})
    original={'diamonds':[{'PricePerCarat':'5000'}]}
    monkeypatch.setattr(module,'parse_excel',lambda p:original)
    worker=Worker(tmp_path/'rate-snapshot.db');snapshots=[]
    worker.prepared.connect(lambda profile,payload:snapshots.append(payload))
    worker.process_inspection(store,('prepare',{'kind':kind},None))
    assert snapshots[0]['diamonds'][0]['PricePerCarat']=='4750.00'
    assert original['diamonds'][0]['PricePerCarat']=='5000'
    store.db.close()


def test_sidebar_history_and_persisted_collapse(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QLabel
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    window=Window(tmp_path/'navigation.db')
    assert not window.sidebar_collapsed and window.pages.currentIndex()==0
    assert not window.dashboard_page.isAncestorOf(window.history)
    assert window.history_page.isAncestorOf(window.history)
    window.nav_buttons[3].click();assert window.pages.currentIndex()==3 and window.page_title.text()=='History'
    window.collapse_button.click();assert window.sidebar_collapsed and window.sidebar.width()==64
    assert [b.toolTip() for b in window.nav_buttons]==window.page_names
    texts=[label.text() for label in window.findChildren(QLabel)]
    assert not any('INVENTORY MANAGEMENT' in value or 'queue acceptance for processing' in value for value in texts)
    window.tray.hide();window.timer.stop();window.store.db.close();window.deleteLater();app.processEvents()
    restored=Window(tmp_path/'navigation.db');assert restored.sidebar_collapsed and restored.pages.currentIndex()==0
    restored.collapse_button.click();assert restored.sidebar.width()==224
    restored.tray.hide();restored.timer.stop();restored.store.db.close();restored.deleteLater();app.processEvents()


def test_legacy_rate_requires_explicit_correction(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    path=tmp_path/'legacy-rate.db';store=Store(path);store.set('website_rates',{'drc':{'mode':'increase','percent':'1000'}});store.db.close()
    window=Window(path)
    assert window.store.get('website_rates',{})['drc']['percent']=='1000'
    assert '1000' in window.rate_saved.text() and not window.rate_rows['drc']['save'].isEnabled()
    assert window.rate_percent.maximum()==100
    window.rate_percent.setValue(99);window.save_rate()
    assert window.store.get('website_rates',{})['drc']['percent']=='99.0'
    assert not window.rate_rows['drc']['invalid']
    window.tray.hide();window.timer.stop();window.store.db.close();window.deleteLater();app.processEvents()


def test_website_rows_save_independently(tmp_path,monkeypatch):
    import drc_sync.ui as ui
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    monkeypatch.setattr(ui,'WEBSITES',({'id':'drc','name':'DRC'},{'id':'mock','name':'Mock website'}))
    window=Window(tmp_path/'multi-site.db');assert window.rate_table.rowCount()==2
    window.rate_percent.setValue(10);window.save_rate()
    row=window.rate_rows['mock'];row['mode'].setCurrentIndex(1);row['percent'].setValue(20);window.save_rate('mock')
    saved=window.store.get('website_rates',{})
    assert saved['drc']=={'mode':'discount','percent':'10.0'}
    assert saved['mock']=={'mode':'increase','percent':'20.0'}
    assert not window.worker.manual and window.worker.requests.empty()
    window.tray.hide();window.timer.stop();window.store.db.close();window.deleteLater();app.processEvents()


def test_invalid_saved_rate_blocks_pending_dispatch(tmp_path,profile,monkeypatch):
    import drc_sync.app as module
    from drc_sync.contract import migrate_profile
    profile=migrate_profile(profile)
    path=tmp_path/'invalid-rate-worker.db';store=Store(path);profile['upload_on_save']=True
    store.set('profiles',[profile]);store.set('website_rates',{'drc':{'mode':'increase','percent':'1000'}})
    job_id=store.enqueue(profile['id'],{'diamonds':[{'PricePerCarat':'5000'}]},profile)
    def forbidden(*args,**kwargs):raise AssertionError('Invalid rate must not dispatch or read credentials')
    monkeypatch.setattr(module,'deliver',forbidden);monkeypatch.setattr(module,'read_key',forbidden)
    worker=Worker(path);worker.start()
    try:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            row=store.db.execute('SELECT state,detail FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row['state']=='blocked':break
            time.sleep(.1)
        assert row['state']=='blocked' and 'Change Rates' in row['detail']
        assert store.get('website_rates',{})['drc']['percent']=='1000'
    finally:
        worker.requestInterruption();worker.wait();store.db.close()


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


def test_plus_minus_and_visible_percentage_keyboard_entry(tmp_path,monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    w=Window(tmp_path/'numeric-entry.db');w.resize(1060,760);w.navigate(2);w.show();app.processEvents()
    w.rate_mode.buttons[1].click();assert w.rate_mode.currentText()=='Increase' and w.rate_percent.maximum()==100
    w.rate_percent.setFocus();w.rate_percent.selectAll();QTest.keyClicks(w.rate_percent,'25.50');w.save_rate()
    assert w.store.get('website_rates',{})['drc']=={'mode':'increase','percent':'25.5'}
    assert w.rate_percent.text()=='25.50'
    assert w.rate_percent.width()>w.rate_percent.fontMetrics().horizontalAdvance('100.00')+20
    assert w.rate_percent.height()>=40
    w.rate_mode.buttons[0].click();assert w.rate_mode.currentText()=='Discount' and w.rate_percent.maximum()==99.99
    w.tray.hide();w.timer.stop();w.store.db.close();w.hide();w.deleteLater();app.processEvents()


def test_invalid_numeric_text_does_not_change_saved_rate(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    w=Window(tmp_path/'invalid-numeric.db');w.rate_mode.buttons[1].click();w.rate_percent.setValue(10);w.save_rate()
    saved=w.store.get('website_rates',{});warnings=[];monkeypatch.setattr(QMessageBox,'warning',lambda *args:warnings.append(args))
    for invalid in ('100.01','','NaN','1,0'):
        w.rate_percent.setText(invalid);w.save_rate()
        assert w.store.get('website_rates',{})==saved
    assert len(warnings)==4
    w.tray.hide();w.timer.stop();w.store.db.close();w.deleteLater();app.processEvents()


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
