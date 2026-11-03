import os,json
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QDialog,QPlainTextEdit
from drc_sync.app import Window,Worker
from drc_sync.portal_store import PortalStore

def cleanup(w,app):
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
    assert json.loads(w.store.db.execute('SELECT payload FROM portal_jobs').fetchone()[0])['diamonds'][0]['PricePerCarat']=='112.50'
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
