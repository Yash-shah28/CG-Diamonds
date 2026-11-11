import os,uuid,json
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from drc_sync.portal_store import PortalStore
from drc_sync.app import Window,Worker
from drc_sync import portal_ui as ui


def event(result='revoked_key',company=True):
    return dict(request_id=str(uuid.uuid4()),time='2026-11-11T05:49:00Z',client_id='a'*32 if company else None,company_name='DRC' if company else 'Untrusted guess',key_fingerprint='abcd12a82f19',result=result,http_status=200 if result=='accepted' else 401,ip='127.0.0.1',user_agent='Mock fetcher',method='GET')


def test_audit_persistence_deduplication_and_secret_redaction(tmp_path):
    store=PortalStore(tmp_path/'audit.db');item=event();item['user_agent']='cg_read_'+('secret'*8)
    store.save_auth_audits([item,item]);row=store.db.execute('SELECT * FROM portal_auth_audit').fetchall();assert len(row)==1 and 'cg_read_' not in row[0]['payload']
    unknown=event('unknown_key',False);store.save_auth_audits([unknown]);data=json.loads(store.db.execute('SELECT payload FROM portal_auth_audit WHERE request_id=?',(unknown['request_id'],)).fetchone()[0]);assert data['company_name']=='Unknown company' and data['client_id'] is None
    bad=event();bad['key_fingerprint']='cg_read_not-a-fingerprint';store.save_auth_audits([bad]);assert store.db.execute('SELECT count(*) FROM portal_auth_audit').fetchone()[0]==2
    store.db.close();again=PortalStore(tmp_path/'audit.db');assert again.db.execute('SELECT count(*) FROM portal_auth_audit').fetchone()[0]==2;again.db.close()


def test_authentication_history_filters_and_details(tmp_path,monkeypatch):
    store=PortalStore(tmp_path/'audit.db');records=[event(result, result not in ('missing_key','unknown_key')) for result in ['accepted','missing_key','unknown_key','revoked_key','suspended_company']]
    records[1]['key_fingerprint']=None;store.save_auth_audits(records);store.db.close()
    app=QApplication.instance() or QApplication([]);monkeypatch.setattr(Worker,'start',lambda self:None);monkeypatch.setattr(ui,'start_activity_refresh',lambda *a,**k:None)
    w=Window(tmp_path/'audit.db')
    try:
        w.history_filter.setCurrentText('Authentication');assert w.history.rowCount()==5
        w.history_filter.setCurrentText('Revoked key');assert w.history.rowCount()==1
        assert w.history.horizontalHeaderItem(1).text()=='Company'
        assert w.history.item(0,1).text()=='DRC' and w.history.item(0,3).text()=='401' and 'a82f19' in w.history.item(0,4).text()
        detail=w.history_details[0];assert 'DRC' in detail and 'abcd12a82f19' in detail and '401' in detail and 'Request ID:' in detail and '127.0.0.1' in detail
        w.history_filter.setCurrentText('Unknown key');assert w.history.rowCount()==1 and 'Unknown company' in w.history_details[0]
        w.history_filter.setCurrentText('Missing key');assert w.history.rowCount()==1 and 'Unknown company' in w.history_details[0]
    finally:
        w.timer.stop();w.tray.hide();w.store.db.close();w.deleteLater();app.processEvents()
