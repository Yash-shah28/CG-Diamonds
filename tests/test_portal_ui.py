import os,json
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QDialog,QPlainTextEdit
from drc_sync.app import Window,Worker
from drc_sync.portal_store import PortalStore

def cleanup(w,app):
    worker=getattr(w,'_activity_worker',None)
    if worker:
        worker.requestInterruption();worker.wait(500)
    w.tray.hide();w.timer.stop();w.store.db.close();w.deleteLater();app.processEvents()

def test_five_pages_sidebar_and_persistence(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    w=Window(tmp_path/'ui.db');assert w.pages.count()==5
    assert w.page_names==['Dashboard','Excel Mapping','API Access','Change Rates','History']
    for i,name in enumerate(w.page_names):w.navigate(i);assert w.page_title.text()==name
    w.set_sidebar_collapsed(True);cleanup(w,app)
    w=Window(tmp_path/'ui.db');assert w.sidebar_collapsed and w.pages.currentIndex()==0;cleanup(w,app)

def test_rates_are_company_rows_and_save_republishes(prepared,monkeypatch):
    s,p=prepared;cid=s.add_company('Website','dev@example.com',['Natural'],secret_writer=lambda *a:None)
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    w=Window(s.db.execute('PRAGMA database_list').fetchone()[2]);w.navigate(3);row=w.rate_rows[cid]
    row['mode'].setCurrentIndex(1);row['percent'].setValue(12.5);w.save_rate(cid)
    assert w.store.company(cid)['percent']=='12.5'
    assert json.loads(w.store.db.execute('SELECT payload FROM portal_jobs').fetchone()[0])['natural'][0]['PricePerCarat']=='112.50'
    assert w.clients_table.rowCount()==1;cleanup(w,app)

def test_new_error_not_suppressed_by_previous_different_error(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    w=Window(tmp_path/'notify.db');messages=[];monkeypatch.setattr(w.tray,'showMessage',lambda *args:messages.append(args))
    w.notify_problem('natural','Row 2, Weight: invalid number');w.notify_problem('natural','Row 2, Weight: invalid number');w.notify_problem('natural','Row 5, Color: missing')
    assert len(messages)==2 and 'Row 5' in messages[-1][1];cleanup(w,app)

def test_history_full_error_details_and_new_events(prepared,monkeypatch):
    s,_=prepared;cid=s.add_company('Website','dev@example.com',['Natural'],secret_writer=lambda *a:None);s.history(cid,'Publish','Published to API','Snapshot confirmed, import not confirmed.',1)
    s.error('Natural','Natural - stock.xlsx\nRow 3, Weight: invalid numeric value.')
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    w=Window(s.db.execute('PRAGMA database_list').fetchone()[2]);assert w.metric_values['Sent to DRC'].text()=='1'
    text=[];monkeypatch.setattr(QDialog,'exec',lambda d:(text.append(d.findChild(QPlainTextEdit).toPlainText()) or QDialog.Rejected))
    w.history_filter.setCurrentText('Failure');assert w.history.rowCount()==1;w.open_history_response(0)
    assert 'Row 3, Weight' in text[0]
    w.history_filter.setCurrentText('Published to API');assert w.history.rowCount()==1;cleanup(w,app)

from test_portal import prepared

def test_feedback_dialog_rendering(prepared, monkeypatch):
    from drc_sync.portal_ui import show_feedback_dialog, FeedbackFetchThread
    from drc_sync.portal_client import PortalClient
    s, _ = prepared
    cid = s.add_company('Website', 'dev@example.com', ['Natural'], secret_writer=lambda *a: None)
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    w = Window(s.db.execute('PRAGMA database_list').fetchone()[2])

    fake_feedback = {
        'ok': True,
        'latest_feedback': {
            'success_count': 680,
            'failed_count': 2,
            'total_reported': 682,
            'comment': 'Test sync',
            'failures': [{'stock_id': 'NAT-1', 'reason': 'Cut missing'}],
            'recorded_at': '2026-11-05T00:00:00Z'
        },
        'latest_access': {
            'fetch_count': 10,
            'last_fetched_at': '2026-11-05T13:45:00.000Z',
            'natural_count': 276,
            'lab_grown_count': 0,
            'total_count': 276,
            'last_ip': '103.50.153.226',
            'last_user_agent': 'curl/8.12.0'
        }
    }
    # Synchronously run the thread
    class SyncThread:
        def __init__(self, *a, **k):
            self.finished = type('Signal', (), {'connect': lambda self, fn: setattr(self, 'fn', fn)})()
        def start(self):
            if hasattr(self.finished, 'fn'):
                self.finished.fn(fake_feedback, '')
        def wait(self):
            pass
    monkeypatch.setattr('drc_sync.portal_ui.FeedbackFetchThread', SyncThread)
    monkeypatch.setattr(QDialog, 'exec', lambda self: None)
    show_feedback_dialog(w, cid)
    cleanup(w, app)


def test_activity_summary_and_cell(prepared):
    from drc_sync.portal_ui import format_access_summary, _make_activity_cell
    # None or empty
    assert format_access_summary(None) is None
    assert format_access_summary({}) is None
    assert format_access_summary({'fetch_count': 0}) is None

    # Full data with counts
    acc = {
        'fetch_count': 10,
        'last_fetched_at': '2026-11-05T13:45:00.000Z',
        'natural_count': 276,
        'lab_grown_count': 15,
        'total_count': 291,
        'last_ip': '103.50.153.226',
        'last_user_agent': 'curl/8.12.0'
    }
    info = format_access_summary(acc)
    assert info is not None
    assert '10 fetches' in info['header']
    assert '276 Natural, 15 Lab Grown (291 Total)' in info['diamonds']
    assert info['ip'] == '103.50.153.226'

    cell = _make_activity_cell(acc)
    assert cell is not None
    assert '276 Natural' in cell.toolTip()
