import json
import sqlite3
import httpx
import pytest
from drc_sync.core import Store, deliver
from drc_sync.contract import ENDPOINT
from drc_sync.responses import MAX_BODY, safe_body, response_description

@pytest.fixture
def setup_request(tmp_path):
    store=Store(tmp_path/'responses.db')
    profile={'id':'natural','kind':'Natural','path':'stock.xlsx','endpoint':ENDPOINT,'confirmed':False,'retry_safe':True}
    return store,profile,{'diamonds':[{'StockId':'A1','Weight':'1.01'}]}

def job_for(store,profile,payload,manual=True):
    n=store.enqueue_manual(profile['id'],payload) if manual else store.enqueue(profile['id'],payload)
    return store.db.execute('SELECT * FROM jobs WHERE id=?',(n,)).fetchone()

def latest(store):
    return store.db.execute('SELECT * FROM responses ORDER BY id DESC LIMIT 1').fetchone()

@pytest.mark.parametrize('status,body,expected',[(200,b'{"accepted":1}','review'),(202,b'{"job_id":1}','review'),(200,b'plain accepted','review'),(204,b'','review'),(400,b'{"error":"bad row"}','failed'),(401,b'Unauthorized','failed'),(429,b'Rate limited','review'),(500,b'Server failure','review')])
def test_manual_capture_without_success_rule(setup_request,status,body,expected):
    store,p,payload=setup_request; job=job_for(store,p,payload)
    requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(status,content=body)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        deliver(store,job,p,'mock-secret',client,manual=True)
    response=latest(store)
    assert len(requests)==1 and response['status']==status
    assert response['elapsed_ms']>=0 and response['state']==expected
    saved=store.db.execute('SELECT * FROM jobs').fetchone()
    assert saved['state']==expected and saved['manual']==1 and saved['attempts']==1
    assert saved['due']<=__import__('time').time()+1
    if not body: assert response['body']==''
    elif body.startswith(b'{'): assert json.loads(response['body'])==json.loads(body)
    else: assert response['body']==body.decode()
    assert 'Needs review' in response_description(saved,response) if expected=='review' else True

def test_automatic_still_blocks_and_manual_job_cannot_run_as_automatic(setup_request):
    store,p,payload=setup_request
    job=job_for(store,p,payload,manual=False)
    def forbidden(request): raise AssertionError('Automatic upload must not happen')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        deliver(store,job,p,'secret',client)
        manual_job=job_for(store,p,payload)
        p['confirmed']=True; p['success_field']='success'
        deliver(store,manual_job,p,'secret',client)
    assert latest(store) is None
    blocked=store.db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone()
    assert 'No request sent' in response_description(blocked)

def test_manual_partial_success_never_marks_complete(setup_request):
    store,p,payload=setup_request; p.update(confirmed=True,success_field='success')
    job=job_for(store,p,payload)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'success':True,'errors':['bad row']}))) as client:
        deliver(store,job,p,'secret',client,manual=True)
    assert latest(store)['state']=='review'

def test_known_complete_success_can_be_validated(setup_request):
    store,p,payload=setup_request; p.update(confirmed=True,success_field='success')
    job=job_for(store,p,payload)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'success':True}))) as client:
        deliver(store,job,p,'secret',client,manual=True)
    assert latest(store)['state']=='success'

def test_network_failure_saved_without_secrets_or_retry(setup_request):
    store,p,payload=setup_request; job=job_for(store,p,payload)
    def fail(request): raise httpx.ReadTimeout('Authorization: Bearer private-token secret-value',request=request)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        deliver(store,job,p,'secret-value',client,manual=True)
    response=latest(store)
    assert response['status'] is None and response['state']=='review'
    assert 'private-token' not in str(dict(response)) and 'secret-value' not in str(dict(response))
    assert 'No server response' in response_description(job,response)
    assert store.db.execute("SELECT COUNT(*) FROM jobs WHERE state='retry'").fetchone()[0]==0

def test_redacts_json_and_plaintext_credentials(setup_request):
    store,p,payload=setup_request; job=job_for(store,p,payload)
    body={'headers':{'X-API-Key':'other-key','Authorization':'Bearer other-token'},'nested':[{'access_token':'access-secret','password':'pw-secret'}],'message':'echo active-secret; Authorization=Basic encoded-secret'}
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(400,json=body))) as client:
        deliver(store,job,p,'active-secret',client,manual=True)
    response=latest(store); saved=str(dict(response))
    assert json.loads(response['body'])['headers']['X-API-Key']=='[REDACTED]'
    for secret in ('other-key','other-token','access-secret','pw-secret','active-secret','encoded-secret'):
        assert secret not in saved
    assert '[REDACTED]' in response['body']
    text,_=safe_body(b'x-api-key: plain-key\nAuthorization: Bearer plain-token\n{"api_key":"broken-token',())
    for secret in ('plain-key','plain-token','broken-token'): assert secret not in text

@pytest.mark.parametrize('raw',[b'A'*(MAX_BODY+1),('é'*(MAX_BODY//2+1)).encode()],ids=['ascii-over-limit','utf8-over-limit'])
def test_oversized_bodies_are_bounded_and_marked(raw):
    text,truncated=safe_body(raw)
    assert truncated and len(text.encode())<=MAX_BODY and 'omitted' in text

def test_stream_capture_stops_at_limit(setup_request):
    store,p,payload=setup_request; job=job_for(store,p,payload)
    class Large(httpx.SyncByteStream):
        chunks=0
        closed=False
        def __iter__(self):
            for _ in range(200):
                self.chunks+=1
                yield b'X'*16384
        def close(self): self.closed=True
    stream=Large()
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=stream))) as client:
        deliver(store,job,p,'secret',client,manual=True)
    assert stream.chunks<200 and stream.closed
    assert latest(store)['truncated']==1 and latest(store)['state']=='review'

def test_attempts_preserved_and_survive_restart(setup_request):
    store,p,payload=setup_request; p.update(confirmed=True,success_field='success')
    job=job_for(store,p,payload,manual=False)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(500,text='server unavailable'))) as client:
        deliver(store,job,p,'secret',client)
    job=store.db.execute('SELECT * FROM jobs').fetchone()
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'success':True}))) as client:
        deliver(store,job,p,'secret',client)
    assert [r[0] for r in store.db.execute('SELECT attempt FROM responses ORDER BY id')]==[1,2]
    path=store.db.execute('PRAGMA database_list').fetchone()[2]; store.db.close()
    restored=Store(path)
    assert restored.db.execute('SELECT COUNT(*) FROM responses').fetchone()[0]==2

def test_old_database_upgrade_preserves_history(tmp_path):
    path=tmp_path/'old.db'; db=sqlite3.connect(path)
    db.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY,profile TEXT,digest TEXT,payload TEXT,state TEXT,attempts INTEGER DEFAULT 0,due REAL DEFAULT 0,created REAL,detail TEXT DEFAULT '',records INTEGER)")
    db.execute("INSERT INTO jobs(id,profile,state,records) VALUES (1,'natural','blocked',270)")
    db.commit(); db.close(); store=Store(path)
    job=store.db.execute('SELECT * FROM jobs').fetchone()
    assert job['records']==270 and job['manual']==0
    assert response_description(job)=='No request sent—no server response.'

def test_undispatched_manual_jobs_do_not_resume(setup_request):
    store,p,payload=setup_request; job=job_for(store,p,payload)
    path=store.db.execute('PRAGMA database_list').fetchone()[2]; store.db.close()
    restored=Store(path)
    assert restored.db.execute('SELECT state FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='review'
    assert restored.db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('pending','retry') AND manual=0").fetchone()[0]==0

@pytest.mark.parametrize('rule',[None,'',5,False,'   '],ids=['missing','empty','number','boolean','whitespace'])
def test_malformed_success_rules_block_automatic_requests(setup_request,rule):
    store,p,payload=setup_request; p.update(confirmed=True,success_field=rule)
    job=job_for(store,p,payload,manual=False)
    def forbidden(request): raise AssertionError('No request should be sent')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        deliver(store,job,p,'secret',client)
    assert latest(store) is None
    assert store.db.execute('SELECT state FROM jobs').fetchone()[0]=='blocked'

@pytest.mark.parametrize('body,expected', [
    ({'success': True, 'message': 'Push queued successfully'}, True),
    ({'success': True, 'message': 'Push queued successfully', 'errors': ['invalid']}, False),
    ({'success': False, 'message': 'Push queued successfully'}, False),
    ({'success': True, 'message': 'Still processing'}, False),
])
def test_sent_acknowledgement(body, expected):
    import json
    from drc_sync.responses import queued_acceptance
    assert queued_acceptance(200, json.dumps(body)) is expected
    assert not queued_acceptance(500, json.dumps(body))
