import json
import sqlite3
import httpx
import pytest
from drc_sync.core import Store,deliver
from drc_sync.contract import ENDPOINT
from drc_sync.diagnostics import run_preflight

@pytest.fixture
def request_data(tmp_path):
    store=Store(tmp_path/'failures.db')
    p={'id':'natural','kind':'Natural','path':'inventory.xlsx','endpoint':ENDPOINT,'confirmed':False,'retry_safe':True}
    n=store.enqueue_manual('natural',{'diamonds':[{'StockId':'A1'}]})
    job=store.db.execute('SELECT * FROM jobs WHERE id=?',(n,)).fetchone()
    return store,p,job

def test_client_setup_exception_is_definitely_not_sent(request_data,monkeypatch):
    import drc_sync.core as core
    store,p,job=request_data
    def fail(**kwargs): raise ImportError('Authorization: Bearer SECRET-VALUE')
    monkeypatch.setattr(core.httpx,'Client',fail)
    result=deliver(store,job,p,'SECRET-VALUE',manual=True)
    assert result['saved'] and result['response']['state']=='blocked'
    assert 'client setup' in result['response']['error']
    assert 'ImportError' in result['response']['error']
    assert 'No request sent' in result['response']['error']
    assert 'SECRET-VALUE' not in str(result)

def test_dispatch_unexpected_error_is_uncertain(request_data):
    store,p,job=request_data
    def fail(request): raise RuntimeError('SECRET-VALUE')
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        result=deliver(store,job,p,'SECRET-VALUE',client,manual=True)
    assert result['response']['state']=='review'
    assert 'request dispatch' in result['response']['error']
    assert 'remote outcome uncertain' in result['response']['error']
    assert 'SECRET-VALUE' not in str(result)

def test_stream_interruption_preserves_status_without_partial_secret(request_data):
    store,p,job=request_data
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b'A'*16384
            raise httpx.ReadError('SECRET-VALUE')
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Interrupted()))) as client:
        result=deliver(store,job,p,'SECRET-VALUE',client,manual=True)
    assert result['response']['status']==200 and result['response']['state']=='review'
    assert 'response reading' in result['response']['error']
    assert 'Partial response body omitted' in result['response']['body']
    assert 'SECRET-VALUE' not in str(result)

def test_processing_failure_preserves_complete_response(request_data):
    store,p,job=request_data
    class BadRule(str):
        def split(self,*args): raise RuntimeError('SECRET-VALUE')
    p.update(confirmed=True,success_field=BadRule('success'))
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'success':True,'uploadId':1671}))) as client:
        result=deliver(store,job,p,'SECRET-VALUE',client,manual=True)
    assert result['response']['status']==200
    assert json.loads(result['response']['body'])['uploadId']==1671
    assert 'response processing' in result['response']['error']
    assert result['response']['state']=='review'

def test_response_history_failure_returns_safe_in_memory_result(request_data,monkeypatch):
    store,p,job=request_data
    def fail(*a,**k): raise sqlite3.OperationalError('disk failed SECRET-VALUE')
    monkeypatch.setattr(store,'record_response',fail)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'success':True,'uploadId':1671}))) as client:
        result=deliver(store,job,p,'SECRET-VALUE',client,manual=True)
    assert not result['saved']
    assert result['response']['status']==200
    assert json.loads(result['response']['body'])['uploadId']==1671
    assert 'history saving' in result['response']['error']
    assert 'OperationalError' in result['response']['error']
    assert 'SECRET-VALUE' not in str(result)
    assert store.db.execute('SELECT state FROM jobs').fetchone()[0]=='review'

def test_finish_failure_does_not_discard_saved_body(request_data,monkeypatch):
    store,p,job=request_data
    def fail(*a,**k): raise OSError('SECRET-VALUE')
    monkeypatch.setattr(store,'finish',fail)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(400,text='row rejected'))) as client:
        result=deliver(store,job,p,'SECRET-VALUE',client,manual=True)
    assert not result['saved'] and result['response']['body']=='row rejected'
    assert store.db.execute('SELECT body FROM responses').fetchone()[0]=='row rejected'
    assert 'SECRET-VALUE' not in str(result)

def test_preflight_is_offline_and_never_returns_keys(monkeypatch):
    calls=[]
    class Client:
        def __init__(self,**kwargs): calls.append('setup')
        def close(self): calls.append('close')
        def send(self,*a,**k): raise AssertionError('No HTTP allowed')
    report=run_preflight('Natural',lambda k:'SECRET-VALUE',Client)
    assert report['ok'] and report['requests_sent']==0 and report['offline']
    assert calls==['setup','close'] and 'SECRET-VALUE' not in str(report)

def test_preflight_names_failed_stage_without_exception_message():
    def bad_key(kind): raise RuntimeError('SECRET-VALUE')
    def bad_client(**kwargs): raise ImportError('SECRET-VALUE')
    report=run_preflight('Natural',bad_key,bad_client)
    assert not report['ok']
    assert [c['stage'] for c in report['checks']]==['credential lookup','client setup']
    assert 'RuntimeError' in report['checks'][0]['detail']
    assert 'ImportError' in report['checks'][1]['detail']
    assert 'SECRET-VALUE' not in str(report)
