import json,time,hashlib
from pathlib import Path
import pytest,httpx
from drc_sync.portal_store import PortalStore
from drc_sync.portal_client import PortalClient,HostingError
from drc_sync.pull_worker import PullWorker
from drc_sync.core import WorkbookValidationError,parse_excel
from drc_sync.workbook_errors import workbook_error
from drc_sync.contract import new_profile

@pytest.fixture
def prepared(tmp_path):
    from openpyxl import Workbook
    store=PortalStore(tmp_path/'portal.db');profiles=[]
    for kind in ('Natural','Lab Grown'):
        p=new_profile();p.update(id=kind,kind=kind,path=str(tmp_path/(kind+'.xlsx')),sheet='Stock',enabled=True,mapping={'Stock #':'StockId','Weight':'Weight','Shape':'Shape','Color':'Color','Clarity':'Clarity','PPC':'PricePerCarat'})
        w=Workbook();s=w.active;s.title='Stock';s.append(['Stock #','Weight','Shape','Color','Clarity','PPC']);s.append(['A1',1.01,'Round','D','VVS1','100']);w.save(p['path']);profiles.append(p)
    store.set('profiles',profiles)
    for p in profiles:store.cache(p,parse_excel(p))
    yield store,profiles
    store.db.close()

def add(store,kinds=None,**kwargs):return store.add_company('Company','dev@example.com',kinds or ['Natural'],secret_writer=lambda *a:None,**kwargs)

def test_independent_company_prices_and_original_inventory(prepared):
    s,p=prepared;a=add(s,mode='increase',percent='10');b=add(s,mode='discount',percent='5')
    assert s.compose(s.company(a))['diamonds'][0]['PricePerCarat']=='110.00'
    assert s.compose(s.company(b))['diamonds'][0]['PricePerCarat']=='95.00'
    assert json.loads(s.db.execute("SELECT payload FROM portal_cache WHERE kind='Natural'").fetchone()[0])['diamonds'][0]['PricePerCarat']=='100'

@pytest.mark.parametrize('mode,percent,valid',[('increase','100',True),('increase','100.01',False),('discount','99.99',True),('discount','100',False),('discount','-1',False)])
def test_company_boundaries(prepared,mode,percent,valid):
    s,_=prepared
    if valid:assert add(s,mode=mode,percent=percent)
    else:
        with pytest.raises(ValueError):add(s,mode=mode,percent=percent)

def test_scope_and_combined_type_identity(prepared):
    s,_=prepared;a=add(s,['Lab Grown']);b=add(s,['Natural','Lab Grown'])
    assert [d['Diamond Type'] for d in s.compose(s.company(a))['diamonds']]==['LAB_GROWN']
    assert len(s.compose(s.company(b))['diamonds'])==2

def test_changed_connection_does_not_use_stale_cache(prepared):
    s,p=prepared;c=add(s);p[0]['sheet']='Other';s.set('profiles',p)
    with pytest.raises(WorkbookValidationError,match='connection changed'):s.queue(c)

def test_price_save_queues_immediately_and_supersedes_only_unsent_snapshots(prepared):
    s,_=prepared;c=add(s);first=s.queue(c);s.set_rate(c,'increase','15')
    jobs=s.db.execute('SELECT * FROM portal_jobs ORDER BY id').fetchall()
    assert jobs[0]['state']=='superseded' and jobs[1]['revision']>jobs[0]['revision']
    assert json.loads(jobs[1]['payload'])['diamonds'][0]['PricePerCarat']=='115.00'

def test_access_changes_precede_publication_even_during_retry_backoff(prepared):
    s,_=prepared;c=add(s,['Natural','Lab Grown']);s.queue(c);s.change_scope(c,['Natural'])
    control=s.pending();assert control['action']=='suspend'
    s.finish_portal(control,'failed','offline',retry=True)
    assert s.pending() is None
    s.db.execute('UPDATE portal_jobs SET due=0 WHERE id=?',(control['id'],));s.db.commit()
    control=s.pending();s.finish_portal(control,'suspended','disabled')
    assert s.pending()['action']=='publish'

def test_rotation_preserves_history_and_never_stores_raw_keys(prepared):
    s,_=prepared;captured=[];c=add(s);old=s.company(c)['key_hash'];s.queue(c)
    s.rotate(c,lambda slot,key:captured.append(key))
    assert s.company(c)['key_hash']!=old
    for table in ['portal_companies','portal_jobs','portal_history','settings']:
        assert captured[0] not in repr([tuple(r) for r in s.db.execute('SELECT * FROM '+table)])

def test_legacy_jobs_preserved_and_never_dispatched(prepared,monkeypatch):
    s,p=prepared;legacy=s.enqueue('Natural',{'diamonds':[{'StockId':'OLD'}]});worker=PullWorker(s.db.execute('PRAGMA database_list').fetchone()[2])
    import drc_sync.core as core
    monkeypatch.setattr(core,'deliver',lambda *a,**k:pytest.fail('DRC request attempted'))
    worker.process_inspection(s,('send',p[0],{'diamonds':[{}]}));worker.process_jobs(s)
    row=s.db.execute('SELECT * FROM jobs WHERE id=?',(legacy,)).fetchone()
    assert row['state']=='pending' and 'OLD' in row['payload']

def test_hosting_client_handles_idempotent_publish_without_leaking_admin(prepared):
    s,_=prepared;c=add(s);s.queue(c);job=s.pending();calls=[]
    def handle(request):
        calls.append(request);body=json.loads(request.content)
        assert request.headers['Authorization']=='Bearer cg_admin_secret'
        return httpx.Response(200,json={'ok':True,'revision':job['revision'],'already_published':True})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client=PortalClient({},token='cg_admin_secret',http=http);assert client.publish(job)['revision']==job['revision']
    assert len(calls)==2 and all(r.method=='POST' for r in calls)

@pytest.mark.parametrize('status,retry',[(401,True),(503,True),(409,False),(400,False)])
def test_hosting_errors_do_not_echo_response_secrets(status,retry):
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(status,json={'error':'cg_admin_secret'}))) as http:
        client=PortalClient({},token='cg_admin_secret',http=http)
        with pytest.raises(HostingError) as exc:client.admin()
    assert 'cg_admin_secret' not in str(exc.value) and exc.value.retryable==retry

def test_worker_retry_is_safe_and_does_not_use_drc(prepared,monkeypatch):
    s,_=prepared;c=add(s);s.queue(c)
    import drc_sync.pull_worker as m
    class Offline:
        def __init__(self,*a):raise HostingError('Hosting is not configured.')
    monkeypatch.setattr(m,'PortalClient',Offline);worker=PullWorker(s.db.execute('PRAGMA database_list').fetchone()[2]);worker.process_jobs(s)
    row=s.db.execute('SELECT * FROM portal_jobs').fetchone();assert row['state']=='pending' and row['attempts']==1

def test_interrupted_email_is_review_not_automatic_resend(prepared):
    s,_=prepared;c=add(s);s.db.execute("UPDATE portal_companies SET last_email='Sending' WHERE id=?",(c,));s.db.execute('INSERT INTO portal_mail_requests VALUES (?,?)',(c,'sending'));s.db.commit()
    reopened=PortalStore(s.db.execute('PRAGMA database_list').fetchone()[2],recover=True)
    assert reopened.db.execute('SELECT state FROM portal_mail_requests').fetchone()[0]=='review'
    assert 'Needs review' in reopened.company(c)['last_email'];reopened.db.close()

@pytest.mark.parametrize('exc,expected',[(FileNotFoundError('SECRET'), 'not found'),(PermissionError('SECRET'),'access denied'),(RuntimeError('SECRET'),'RuntimeError')])
def test_safe_workbook_error_types(exc,expected):
    message=workbook_error({'kind':'Natural','path':'stock.xlsx'},exc)
    assert expected in message and 'stock.xlsx' in message and 'SECRET' not in message

def test_exact_row_and_field_survive_notification(prepared):
    from openpyxl import load_workbook
    s,p=prepared;w=load_workbook(p[0]['path']);w['Stock']['B2']='oops';w.save(p[0]['path'])
    with pytest.raises(WorkbookValidationError) as exc:parse_excel(p[0])
    message=workbook_error(p[0],exc.value)
    assert 'Row 2' in message and 'Weight' in message and 'positive' in message

def test_no_notification_secret_echo():
    message=workbook_error({'path':'dsk_should_hide.xlsx','kind':'Natural'},WorkbookValidationError('cg_read_this_must_be_hidden'))
    assert 'this_must_be_hidden' not in message and 'should_hide' not in message

def test_storage_failure_pauses_instead_of_losing_unknown_outcome(prepared,monkeypatch):
    s,_=prepared;worker=PullWorker(s.db.execute('PRAGMA database_list').fetchone()[2]);messages=[];worker.problem.connect(lambda i,d:messages.append(d))
    monkeypatch.setattr(worker,'process_jobs',lambda store:(_ for _ in ()).throw(OSError('cg_admin_DO_NOT_SHOW')))
    worker.pump(s)
    assert worker.paused and len(messages)==1 and 'OSError' in messages[0] and 'DO_NOT_SHOW' not in messages[0]
