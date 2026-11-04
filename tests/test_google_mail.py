from types import SimpleNamespace
import base64,json
import httpx,pytest
from drc_sync.google_mail import send_invitation,MailError

def send(http):
    return send_invitation({'name':'Example','email':'dev@example.com','kinds':'["Natural"]'},'cg_read_TEST','https://example.com/api/v1/inventory',b'%PDF-1.4 fake',http=http,credential_provider=lambda:SimpleNamespace(token='google-token'),sender='owner@example.com')

def test_explicit_gmail_send_contains_key_but_pdf_uses_placeholder():
    calls=[]
    def accept(request):
        calls.append(request);assert request.headers['Authorization']=='Bearer google-token'
        raw=base64.urlsafe_b64decode(json.loads(request.content)['raw']);assert b'cg_read_TEST' in raw
        assert b'Chintamani-Gems-API-Guide.pdf' in raw
        assert b'natural' in raw and b'lab_grown' in raw
        return httpx.Response(200,json={'id':'accepted-id'})
    with httpx.Client(transport=httpx.MockTransport(accept)) as client:assert send(client)=='accepted-id'
    assert len(calls)==1

def test_email_timeout_never_retries():
    calls=[]
    def fail(request):calls.append(request);raise httpx.ReadTimeout('google-token',request=request)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(MailError) as exc:send(client)
    assert len(calls)==1 and exc.value.uncertain and 'google-token' not in str(exc.value)

def test_rejected_mail_not_claimed_sent():
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(401,json={'secret':'google-token'}))) as client:
        with pytest.raises(MailError) as exc:send(client)
    assert not exc.value.uncertain and 'google-token' not in str(exc.value)

def test_pdf_contains_no_raw_credential():
    from io import BytesIO
    from pypdf import PdfReader
    from drc_sync.integration_guide import guide_pdf
    text='\n'.join(p.extract_text() for p in PdfReader(BytesIO(guide_pdf('https://example.com/api/v1/inventory',['Natural','Lab Grown']))).pages)
    assert 'YOUR_API_KEY' in text and 'cg_read_' not in text and 'NATURAL' in text and 'LAB_GROWN' in text

def test_google_pkce_and_loopback_are_used_without_real_login(tmp_path,monkeypatch):
    from drc_sync import google_mail as m
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.oauth2 import id_token
    calls=[];saved={}
    class Flow:
        def run_local_server(self,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(id_token='fake-id',to_json=lambda:'{"refresh_token":"fake-refresh"}')
    def factory(config,scopes,**kwargs):
        assert kwargs['autogenerate_code_verifier'] is True
        assert scopes[-1].endswith('gmail.send')
        return Flow()
    monkeypatch.setattr(InstalledAppFlow,'from_client_config',factory)
    monkeypatch.setattr(id_token,'verify_oauth2_token',lambda *a,**k:{'email':'owner@example.com','email_verified':True})
    monkeypatch.setattr(m.portal_secrets,'put_secret',lambda k,v:saved.update({k:v}))
    p=tmp_path/'oauth.json';p.write_text(json.dumps({'installed':{'client_id':'desktop-id','client_secret':'not-a-user-password','auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'}}))
    assert m.connect_google(str(p))=='owner@example.com'
    assert calls[0]['host']=='127.0.0.1' and calls[0]['port']==0 and calls[0]['open_browser']
    assert 'google-credentials' in saved

def test_google_cancel_has_no_credentials_or_secret_echo(tmp_path,monkeypatch):
    from drc_sync import google_mail as m
    from google_auth_oauthlib.flow import InstalledAppFlow
    class Flow:
        def run_local_server(self,**kwargs):raise TimeoutError('PRIVATE')
    monkeypatch.setattr(InstalledAppFlow,'from_client_config',lambda *a,**k:Flow())
    monkeypatch.setattr(m.portal_secrets,'put_secret',lambda *a:pytest.fail('Cancelled login must not persist credentials'))
    p=tmp_path/'oauth.json';p.write_text(json.dumps({'installed':{'client_id':'id','auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'}}))
    with pytest.raises(MailError) as exc:m.connect_google(str(p))
    assert 'PRIVATE' not in str(exc.value)


def test_smtp_auth_and_sending_with_mock(monkeypatch):
    from drc_sync import google_mail as m
    import smtplib
    logged_in = []
    sent_msgs = []
    class MockSMTP:
        def __init__(self, host, port, **kwargs):
            self.host = host
            self.port = port
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def login(self, user, password):
            logged_in.append((user, password))
            if password == 'bad':
                raise smtplib.SMTPAuthenticationError(535, b'Auth failed')
        def send_message(self, msg):
            sent_msgs.append(msg)

    monkeypatch.setattr(smtplib, 'SMTP_SSL', MockSMTP)
    assert m.test_smtp_auth('owner@example.com', 'goodpass') is True
    with pytest.raises(m.MailError):
        m.test_smtp_auth('owner@example.com', 'bad')

    # Test send_smtp_invitation
    company = {'name': 'Test Co', 'email': 'client@example.com', 'kinds': '["Natural"]'}
    msg_id = m.send_smtp_invitation(company, 'cg_key_123', 'https://example.com/api', b'%PDF fake', sender='owner@example.com', password='secret')
    assert msg_id is not None
    assert len(sent_msgs) == 1
    assert sent_msgs[0]['To'] == 'client@example.com'
    assert b'cg_key_123' in sent_msgs[0].as_bytes()
