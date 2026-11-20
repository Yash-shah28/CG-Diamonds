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
    worker = PullWorker(tmp_path/'portal.db')
    for p in profiles:
        store.cache(p, worker.parse_profile(p))
    yield store,profiles
    store.db.close()

def add(store,kinds=None,**kwargs):return store.add_company('Company','dev@example.com',kinds or ['Natural'],secret_writer=lambda *a:None,**kwargs)

def test_independent_company_prices_and_original_inventory(prepared):
    s,p=prepared;a=add(s,mode='increase',percent='10');b=add(s,mode='discount',percent='5')
    assert s.compose(s.company(a))['natural'][0]['PricePerCarat']=='110.00'
    assert s.compose(s.company(a))['lab_grown']==[]
    assert s.compose(s.company(b))['natural'][0]['PricePerCarat']=='95.00'
    assert s.compose(s.company(b))['lab_grown']==[]
    assert json.loads(s.db.execute("SELECT payload FROM portal_cache WHERE kind='Natural'").fetchone()[0])['diamonds'][0]['PricePerCarat']=='100'

@pytest.mark.parametrize('mode,percent,valid',[('increase','100',True),('increase','100.01',False),('discount','99.99',True),('discount','100',False),('discount','-1',False)])
def test_company_boundaries(prepared,mode,percent,valid):
    s,_=prepared
    if valid:assert add(s,mode=mode,percent=percent)
    else:
        with pytest.raises(ValueError):add(s,mode=mode,percent=percent)

def test_scope_and_combined_type_identity(prepared):
    s,_=prepared;a=add(s,['Lab Grown']);b=add(s,['Natural','Lab Grown'])
    assert [d['Diamond Type'] for d in s.compose(s.company(a))['lab_grown']]==['LAB_GROWN']
    assert s.compose(s.company(a))['natural']==[]
    assert len(s.compose(s.company(b))['natural'])==1
    assert len(s.compose(s.company(b))['lab_grown'])==1

def test_changed_connection_does_not_use_stale_cache(prepared):
    s,p=prepared;c=add(s);p[0]['sheet']='Other';s.set('profiles',p)
    with pytest.raises(WorkbookValidationError,match='connection changed'):s.queue(c)

def test_price_save_queues_immediately_and_supersedes_only_unsent_snapshots(prepared):
    s,_=prepared;c=add(s);first=s.queue(c);s.set_rate(c,'increase','15')
    jobs=s.db.execute('SELECT * FROM portal_jobs ORDER BY id').fetchall()
    assert jobs[0]['state']=='superseded' and jobs[1]['revision']>jobs[0]['revision']
    assert json.loads(jobs[1]['payload'])['natural'][0]['PricePerCarat']=='115.00'

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



def test_pull_inventory_formats_both_workbooks_and_company_rate(tmp_path):
    from openpyxl import Workbook

    store = PortalStore(tmp_path/'inventory.db')
    worker = PullWorker(tmp_path/'inventory.db')
    profiles = []
    for kind in ('Natural', 'Lab Grown'):
        path = tmp_path/f'{kind}.xlsx'
        profile = new_profile()
        profile.update(id=kind, kind=kind, path=str(path), sheet='Stock', mapping={
            'Stock #': 'StockId', 'Weight': 'Weight', 'Shape': 'Shape', 'PPC': 'PricePerCarat',
            'Color': 'Color', 'Clarity': 'Clarity', 'Discount': 'Discount', 'Depth %': 'Depth %',
            'Table %': 'Table', 'Report #': 'Certificate #', 'Photo 2': 'Image 2',
            'Cert Link': 'Certificate Image',
        })
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Stock'
        sheet.append([*profile['mapping'], 'Diamond Image', 'Diamond Image 3'])
        depth, table = (61.9, 58) if kind == 'Natural' else (0.595, 0.64)
        sheet.append(['0001', 1.259, 'Oval', 100.239, None, ' ', -12.3456, depth, table, '00089',
                      'https://example.com/a.jpg', 'https://example.com/report.pdf',
                      'https://example.com/a.jpg', 'https://example.com/b.jpg'])
        sheet.append(['0002', 1, 'Round', 120, None, None, 0, depth, table, 812])
        sheet.append(['0003', 1, 'Round', 120, None, None, 0, depth, table, 813,
                      ' ', None, 'https://example.com/fallback.jpg', 'https://example.com/extra.jpg'])
        if kind == 'Lab Grown':
            for row in (2, 3, 4):
                for column in (8, 9):
                    sheet.cell(row, column).number_format = '0.00%'
        workbook.save(path)
        store.cache(profile, worker.parse_profile(profile))
        profiles.append(profile)

    store.set('profiles', profiles)
    customer = add(store, ['Natural', 'Lab Grown'], mode='increase', percent='10')
    store.queue(customer)
    feed = json.loads(store.pending()['payload'])
    assert set(feed) == {'natural', 'lab_grown'}
    for kind, measurements in (('natural', ('61.90', '58.00')), ('lab_grown', ('59.50', '64.00'))):
        stone, second, fallback = feed[kind]
        assert (stone['Depth %'], stone['Table']) == measurements
        assert stone['PricePerCarat'] == '110.26'
        assert stone['Weight'] == '1.25' and stone['Discount'] == '-12.34'
        assert stone['StockId'] == '0001' and stone['Certificate #'] == '00089'
        assert stone['Color'] == stone['Clarity'] == ''
        assert stone['Certificate Image'] == 'https://example.com/report.pdf'
        assert stone['Image'] == 'https://example.com/a.jpg'
        assert 'Image 2' not in stone and 'Country of Polishing' not in stone
        assert second['Image'] == '' and second['Certificate #'] == '812'
        assert fallback['Image'] == 'https://example.com/fallback.jpg'
        assert second['Weight'] == '1.00' and second['Discount'] == '0.00'
    store.db.close()


def test_blank_required_cell_still_blocks_prepare(prepared):
    from openpyxl import load_workbook

    store, profiles = prepared
    profile = profiles[0]
    workbook = load_workbook(profile['path'])
    workbook['Stock']['F2'].value = None
    workbook.save(profile['path'])
    with pytest.raises(WorkbookValidationError, match='missing PricePerCarat'):
        PullWorker(store.db.execute('PRAGMA database_list').fetchone()[2]).parse_profile(profile)


def test_old_feed_jobs_and_gmail_requests_are_migrated_once(prepared):
    from drc_sync.contract import PULL_FEED_VERSION

    store, _ = prepared
    customer = add(store)
    job_id = store.queue(customer)
    store.db.execute("UPDATE portal_jobs SET state='publishing' WHERE id=?", (job_id,))
    store.db.execute("DELETE FROM settings WHERE key IN (?,?)",
                     ('portal.feed-format.2', f'portal.feed-format.{PULL_FEED_VERSION}'))
    row = store.db.execute("SELECT payload FROM portal_cache WHERE kind='Natural'").fetchone()
    old_payload = json.loads(row['payload'])
    del old_payload['format_version']
    store.db.execute("UPDATE portal_cache SET payload=? WHERE kind='Natural'", (json.dumps(old_payload),))
    store.db.execute("INSERT INTO portal_mail_requests VALUES (?, 'waiting')", (customer,))
    store.db.commit()
    db_path = store.db.execute('PRAGMA database_list').fetchone()[2]

    for _ in range(2):
        with PortalStore(db_path, recover=True).db as connection:
            assert connection.execute('SELECT state FROM portal_jobs WHERE id=?', (job_id,)).fetchone()[0] == 'superseded'
            assert connection.execute('SELECT state FROM portal_mail_requests').fetchone()[0] == 'review'
            assert connection.execute("SELECT COUNT(*) FROM portal_history WHERE state='Needs review'").fetchone()[0] == 1
        connection.close()
    with pytest.raises(WorkbookValidationError, match='new API format'):
        store.compose(store.company(customer))


@pytest.mark.parametrize('mail_outcome,history_state', [
    (None, 'Sent via Yahoo Mail'), ('unknown', 'Needs review'), ('rejected', 'Failure'),
])
def test_worker_attempts_yahoo_invitation_only_once(prepared, monkeypatch, mail_outcome, history_state):
    from drc_sync import pull_worker

    store, _ = prepared
    customer = add(store)
    store.queue(customer)
    store.finish_portal(store.pending(), 'published', 'Hosting confirmed.')
    company = store.company(customer)
    store.db.execute("INSERT INTO portal_mail_requests VALUES (?, 'waiting')", (customer,))
    store.db.commit()

    calls = []

    class Hosting:
        base = 'https://example.com'

        def __init__(self, _):
            pass

        def status(self, client_id):
            assert client_id == customer
            return {'enabled': True, 'key_hash': company['key_hash'], 'revision': company['revision']}

        def close(self):
            pass

    def send(company_record, key, url, pdf, *, sender, password):
        calls.append(company_record['id'])
        assert (key, sender, password) == ('cg_read_TEST', 'owner@yahoo.com', 'fake-app-password')
        assert url.endswith('/api/v1/inventory') and pdf.startswith(b'%PDF')
        if mail_outcome:
            raise pull_worker.MailError('Yahoo Mail delivery test', uncertain=mail_outcome == 'unknown')

    monkeypatch.setattr(pull_worker, 'PortalClient', Hosting)
    monkeypatch.setattr(pull_worker.portal_secrets, 'company_key', lambda *_: 'cg_read_TEST')
    monkeypatch.setattr(pull_worker.portal_secrets, 'get_smtp_credentials',
                        lambda: ('owner@yahoo.com', 'fake-app-password'))
    monkeypatch.setattr(pull_worker, 'send_invitation', send)
    worker = PullWorker(store.db.execute('PRAGMA database_list').fetchone()[2])
    worker.process_email(store)
    worker.process_email(store)
    assert calls == [customer]
    assert store.db.execute("SELECT state FROM portal_history WHERE event='Email'").fetchone()[0] == history_state



@pytest.mark.parametrize('kind', ['Natural', 'Lab Grown'])
def test_rapaport_discount_uses_excel_percentage_scale(prepared, kind):
    from openpyxl import load_workbook

    store, profiles = prepared
    profile = next(p for p in profiles if p['kind'] == kind)
    profile['mapping']['Rapaport Trade Discount %'] = 'Discount'
    workbook = load_workbook(profile['path'])
    sheet = workbook['Stock']
    sheet['G1'] = 'Rapaport Trade Discount %'
    sheet['G2'] = -0.65
    sheet['G2'].number_format = '0.00%'
    sheet.append(['A2', 1.25, 'Oval', None, None, 100, '-65%'])
    sheet.append(['A3', 1.25, 'Oval', None, None, 100, 65])
    sheet.append(['A4', 1.25, 'Oval', None, None, 100, None])
    workbook.save(profile['path'])
    store.set('profiles', profiles)
    worker = PullWorker(store.db.execute('PRAGMA database_list').fetchone()[2])
    store.cache(profile, worker.parse_profile(profile))
    customer = add(store, [kind])
    records = store.compose(store.company(customer))['natural' if kind == 'Natural' else 'lab_grown']
    assert [record['Discount'] for record in records] == ['-65.00', '-65.00', '65.00', '']


def test_format_upgrade_keeps_pending_yahoo_invitations(prepared):
    from drc_sync.contract import PULL_FEED_VERSION

    store, _ = prepared
    customer = add(store)
    old_job = store.queue(customer)
    store.db.execute('DELETE FROM settings WHERE key=?', (f'portal.feed-format.{PULL_FEED_VERSION}',))
    store.db.execute("INSERT INTO portal_mail_requests VALUES (?, 'waiting')", (customer,))
    store.db.commit()
    db_path = store.db.execute('PRAGMA database_list').fetchone()[2]

    reopened = PortalStore(db_path, recover=True)
    try:
        assert reopened.db.execute('SELECT state FROM portal_jobs WHERE id=?', (old_job,)).fetchone()[0] == 'superseded'
        assert reopened.db.execute('SELECT state FROM portal_mail_requests').fetchone()[0] == 'waiting'
    finally:
        reopened.db.close()
