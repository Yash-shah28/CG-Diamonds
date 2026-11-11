import os,random
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Signal,QObject,QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication,QLabel
from drc_sync.app import Window,Worker
from drc_sync.portal_store import PortalStore
from drc_sync import portal_ui as ui


def close(w,app):
    w.timer.stop();w.tray.hide();w.store.db.close();w.deleteLater();app.processEvents()


def test_many_companies_fill_rates_page_and_last_row_accessible(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker,'start',lambda self:None)
    monkeypatch.setattr(ui,'start_activity_refresh',lambda *a,**k:None)
    s=PortalStore(tmp_path/'many.db');rng=random.Random(111)
    for i in range(60):s.add_company(f'Company {i:02d} {rng.randrange(10000)}',f'company{i}@example.com',['Natural'],secret_writer=lambda *a:None)
    s.db.close();w=Window(tmp_path/'many.db');w.navigate(3);w.show()
    try:
        w.resize(1240,1000);app.processEvents();large=w.rate_table.height()
        assert w.rate_table.rowCount()==60 and large>650
        w.rate_table.verticalScrollBar().setValue(w.rate_table.verticalScrollBar().maximum());app.processEvents()
        assert w.rate_table.visualRect(w.rate_table.model().index(59,0)).intersects(w.rate_table.viewport().rect())
        last=w.store.companies()[-1]['id'];w.rate_rows[last]['percent'].setValue(12.5)
        assert w.rate_rows[last]['save'].isEnabled()
        w.resize(980,680);app.processEvents();assert large-w.rate_table.height()>200
        assert (w.rate_table.horizontalHeader().length() <= w.rate_table.viewport().width() or w.rate_table.horizontalScrollBar().maximum()>0)
    finally:close(w,app)


def test_activity_worker_reports_failures_without_claiming_not_fetched(monkeypatch):
    class Client:
        def __init__(self,*a):pass
        def status(self,cid):raise RuntimeError('private secret must not appear')
        def close(self):pass
    monkeypatch.setattr(ui,'PortalClient',Client)
    worker=ui.ActivityFetchWorker({},['drc']);errors=[];worker.access_failed.connect(lambda *args:errors.append(args));worker.run()
    assert errors and 'private secret' not in errors[0][1]
    app=QApplication.instance() or QApplication([])
    cell=ui._make_activity_cell(None,error=errors[0][1]);assert 'unavailable' in cell.findChild(QLabel).text()


def test_completed_poll_can_be_restarted_and_fetch_reaches_dashboard(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None)
    s=PortalStore(tmp_path/'poll.db');cid=s.add_company('DRC','test@example.com',['Natural'],secret_writer=lambda *a:None);s.db.close()
    class Poll(QObject):
        access_updated=Signal(str,object);access_failed=Signal(str,str);audit_updated=Signal(object);finished=Signal()
        def __init__(self,*a,**kwargs):super().__init__();self.running=False;self.audit_cursor=kwargs.get("audit_cursor")
        def isRunning(self):return self.running
        def start(self):self.running=True
    monkeypatch.setattr(ui,'ActivityFetchWorker',Poll)
    w=Window(tmp_path/'poll.db')
    try:
        first=w._activity_worker
        from test_auth_audit import event
        first.audit_updated.emit({'entries':[event()], 'next_cursor':'next-page'})
        assert w.store.db.execute('SELECT count(*) FROM portal_auth_audit').fetchone()[0]==1
        first.access_failed.emit(cid,'Hosting could not be checked')
        w.refresh();assert 'unavailable' in w.dashboard_fetch_info.text()
        first.access_updated.emit(cid,{'client_id':cid,'company_name':'DRC','fetch_count':2,'last_fetched_at':'2026-11-11T10:00:00Z','natural_count':270,'lab_grown_count':0,'total_count':270})
        assert 'DRC' in w.dashboard_fetch_info.text() and '270' in w.dashboard_fetch_info.text()
        first.running=False;first.finished.emit();assert w._activity_worker is None
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        ui.load_older_auth_audits(w);assert w._activity_worker is not first
        assert w._activity_worker.audit_cursor=='next-page'
        second=w._activity_worker;second.running=False;second.finished.emit()
        w.refresh();assert '270' in w.dashboard_fetch_info.text()
    finally:close(w,app)
