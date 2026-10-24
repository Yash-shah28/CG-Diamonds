import json
import httpx
import pytest
from openpyxl import Workbook
from drc_sync.core import Store, parse_excel, deliver, ENDPOINT

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'test.db')

@pytest.fixture
def profile(tmp_path):
    path = tmp_path/'inventory.xlsx'
    wb = Workbook(); ws=wb.active; ws.title='Stock'
    ws.append(['Stock No','Weight']); ws.append(['A1',1.25]); wb.save(path)
    return {'id':'natural','kind':'Natural','path':str(path),'sheet':'Stock',
            'mapping':{'Stock No':'stock_id','Weight':'carat'},'required':['stock_id'],
            'numeric':['carat'],'identity':'stock_id','confirmed':True,
            'success_field':'success','success_value':True,'endpoint':ENDPOINT}

def test_mapping(profile):
    assert parse_excel(profile)=={'diamonds':[{'stock_id':'A1','carat':1.25}]}

@pytest.mark.parametrize('value',[None,'bad',-1,'=1+1'])
def test_invalid_numeric(profile,value):
    wb=Workbook(); ws=wb.active; ws.title='Stock'; ws.append(['Stock No','Weight']); ws.append(['A',value]); wb.save(profile['path'])
    with pytest.raises(ValueError): parse_excel(profile)

def test_missing_column(profile):
    profile['mapping']={'Missing':'stock_id'}
    with pytest.raises(ValueError): parse_excel(profile)

def test_queue_duplicates_coalesce_and_profiles(store):
    a={'diamonds':[{'id':'a'}]}; b={'diamonds':[{'id':'b'}]}
    first=store.enqueue('natural',a)
    assert store.enqueue('natural',a) is None
    second=store.enqueue('natural',b)
    assert second != first
    assert store.db.execute('SELECT state FROM jobs WHERE id=?',(first,)).fetchone()[0]=='superseded'
    assert store.enqueue('lab',b) is not None

@pytest.mark.parametrize('code,body,state',[(200,{'success':True},'success'),(200,{'success':True,'errors':['bad row']},'review'),(200,{'success':False},'review'),(202,{'job_id':1},'review'),(401,{},'failed'),(500,{},'review'),(429,{},'review')])
def test_api(store,profile,code,body,state):
    jobid=store.enqueue(profile['id'],parse_excel(profile))
    job=store.db.execute('SELECT * FROM jobs WHERE id=?',(jobid,)).fetchone()
    def handler(request):
        assert request.headers['x-api-key']=='test-secret'
        assert request.url==ENDPOINT
        assert json.loads(request.content)['diamonds'][0]['stock_id']=='A1'
        return httpx.Response(code,json=body)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        deliver(store,job,profile,'test-secret',client)
    row=store.db.execute('SELECT * FROM jobs WHERE id=?',(jobid,)).fetchone()
    assert row['state']==state
    assert 'test-secret' not in row['detail']

def test_network_and_safe_retry(store,profile):
    profile['retry_safe']=True
    jobid=store.enqueue('natural',parse_excel(profile)); job=store.db.execute('SELECT * FROM jobs WHERE id=?',(jobid,)).fetchone()
    def fail(request): raise httpx.ConnectError('offline',request=request)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client: deliver(store,job,profile,'secret',client)
    assert store.db.execute('SELECT state FROM jobs').fetchone()[0]=='retry'

def test_restart_recovery(tmp_path):
    path=tmp_path/'restart.db'; store=Store(path)
    store.enqueue('a',{'diamonds':[{'id':1}]})
    store.db.execute("UPDATE jobs SET state='sending'"); store.db.commit(); store.db.close()
    recovered=Store(path)
    assert recovered.db.execute('SELECT state FROM jobs').fetchone()[0]=='review'

def test_contract_gate(store,profile):
    profile['confirmed']=False
    n=store.enqueue('natural',parse_excel(profile)); job=store.db.execute('SELECT * FROM jobs WHERE id=?',(n,)).fetchone()
    deliver(store,job,profile,'secret')
    assert store.db.execute('SELECT state FROM jobs').fetchone()[0]=='blocked'

def test_duplicate_after_restart(tmp_path):
    path = tmp_path/'state.db'
    store = Store(path)
    payload = {'diamonds':[{'stock_id':'A'}]}
    store.enqueue('natural',payload)
    store.db.close()
    store = Store(path)
    assert store.enqueue('natural',payload) is None

def test_modification_changes_payload(store,profile):
    first = store.enqueue('natural',parse_excel(profile))
    wb = Workbook(); ws = wb.active; ws.title='Stock'; ws.append(['Stock No','Weight']); ws.append(['A1',2.0]); wb.save(profile['path'])
    second = store.enqueue('natural',parse_excel(profile))
    assert first != second

def test_bad_workbook_and_temp_name(profile,tmp_path):
    bad = tmp_path/'bad.xlsx'; bad.write_text('not a workbook')
    profile['path'] = str(bad)
    with pytest.raises(Exception): parse_excel(profile)
    profile['path'] = str(tmp_path/'~$inventory.xlsx')
    with pytest.raises(ValueError): parse_excel(profile)

def test_local_errors_are_deduplicated(store):
    store.error('natural', 'Invalid workbook')
    store.error('natural', 'Invalid workbook')
    assert store.db.execute('SELECT COUNT(*) FROM errors').fetchone()[0] == 1

def test_unsafe_endpoint_blocked_without_request(store,profile):
    profile['endpoint']='http://example.org/upload'
    n=store.enqueue('natural',parse_excel(profile))
    job=store.db.execute('SELECT * FROM jobs WHERE id=?',(n,)).fetchone()
    deliver(store,job,profile,'secret')
    assert store.db.execute('SELECT state FROM jobs').fetchone()[0]=='blocked'

def test_save_triggered_upload_without_completion_rule(store, profile):
    profile.update(confirmed=False, success_field='', upload_on_save=True, retry_safe=True)
    payload = parse_excel(profile)
    first = store.enqueue(profile['id'], payload, profile, force=True)
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'success':True,'message':'Push queued successfully','uploadId':99})
    client=httpx.Client(transport=httpx.MockTransport(handler))
    job=store.db.execute('SELECT * FROM jobs WHERE id=?',(first,)).fetchone()
    deliver(store,job,profile,'offline-key',client=client)
    assert len(calls)==1
    assert store.db.execute('SELECT state FROM jobs WHERE id=?',(first,)).fetchone()[0]=='sent'
    second=store.enqueue(profile['id'],payload,profile,force=True)
    assert second != first
    client.close()

@pytest.mark.parametrize('result_type,result,expected', [('n','2.5',2.5),('str','A2','A2')])
def test_lab_formula_uses_cached_result(profile, result_type, result, expected):
    import zipfile
    import xml.etree.ElementTree as ET
    from openpyxl import load_workbook
    profile['kind']='Lab Grown'
    column='B2' if result_type=='n' else 'A2'
    wb=load_workbook(profile['path']); wb['Stock'][column]='=1+1'; wb.save(profile['path']); wb.close()
    with zipfile.ZipFile(profile['path']) as z: contents={n:z.read(n) for n in z.namelist()}
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    root=ET.fromstring(contents['xl/worksheets/sheet1.xml'])
    cell=root.find(f".//s:c[@r='{column}']",ns); cell.set('t',result_type)
    cell.find('s:v',ns).text=result
    contents['xl/worksheets/sheet1.xml']=ET.tostring(root)
    with zipfile.ZipFile(profile['path'],'w') as z:
        for name,data in contents.items(): z.writestr(name,data)
    record=parse_excel(profile)['diamonds'][0]
    assert record['carat' if column=='B2' else 'stock_id']==expected
    assert '=' not in str(record)


def test_lab_formula_missing_result_blocks_upload(profile):
    from openpyxl import load_workbook
    profile['kind']='Lab Grown'
    wb=load_workbook(profile['path']); wb['Stock']['B2']='=1+1'; wb.save(profile['path']); wb.close()
    with pytest.raises(ValueError,match='formula has no saved result'):
        parse_excel(profile)


def test_lab_formula_error_blocks_upload(profile):
    import zipfile
    from openpyxl import load_workbook
    profile['kind']='Lab Grown'
    wb=load_workbook(profile['path']); wb['Stock']['B2']='=1/0'; wb.save(profile['path']); wb.close()
    with zipfile.ZipFile(profile['path']) as z: contents={n:z.read(n) for n in z.namelist()}
    import xml.etree.ElementTree as ET
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    root=ET.fromstring(contents['xl/worksheets/sheet1.xml'])
    cell=root.find(".//s:c[@r='B2']",ns); cell.set('t','e'); cell.find('s:v',ns).text='#DIV/0!'
    contents['xl/worksheets/sheet1.xml']=ET.tostring(root)
    with zipfile.ZipFile(profile['path'],'w') as z:
        for name,data in contents.items(): z.writestr(name,data)
    with pytest.raises(ValueError,match='formula result is an Excel error'):
        parse_excel(profile)


def test_invalid_saved_rate_blocks_direct_delivery(store,profile):
    store.set('website_rates',{'drc':{'mode':'increase','percent':'100.01'}})
    job_id=store.enqueue(profile['id'],parse_excel(profile))
    job=store.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    calls=[]
    with httpx.Client(transport=httpx.MockTransport(lambda request:calls.append(request) or httpx.Response(200,json={'success':True}))) as client:
        deliver(store,job,profile,'mock-key',client=client)
    assert not calls
    assert store.db.execute('SELECT state FROM jobs WHERE id=?',(job_id,)).fetchone()[0]=='blocked'
