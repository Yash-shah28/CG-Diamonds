"""Desktop SMTP (App Password) and OAuth Gmail sending; secrets live only in Windows vault."""
import base64
import json
import smtplib
import ssl
import time
from email.message import EmailMessage
import httpx
from . import portal_secrets

SCOPES = ['openid','https://www.googleapis.com/auth/userinfo.email','https://www.googleapis.com/auth/gmail.send']

class MailError(RuntimeError):
    def __init__(self,message,uncertain=False):super().__init__(message);self.uncertain=uncertain


def test_smtp_auth(sender, password, host='smtp.gmail.com', port=465, timeout=15):
    """Validate Gmail SMTP credentials directly without sending any email."""
    if not sender or not password:
        raise ValueError('Enter your Gmail address and 16-character App Password.')
    sender = sender.strip()
    password = password.strip().replace(' ', '')
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as server:
            server.login(sender, password)
        return True
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError('Gmail rejected the login. Check that 2-Step Verification is on and use a 16-character App Password (not your normal Google account password).') from None
    except Exception as exc:
        raise MailError(f'Could not connect to Gmail SMTP ({type(exc).__name__}): {exc}') from None


def send_smtp_invitation(company, key, api_url, pdf_bytes, sender=None, password=None, host='smtp.gmail.com', port=465, timeout=45):
    """Send an invitation email using Gmail App Password via SMTP SSL."""
    sender = sender or portal_secrets.get_secret('google-smtp-sender') or portal_secrets.get_secret('google-sender')
    password = password or portal_secrets.get_secret('google-smtp-password')
    if not sender or not password:
        raise MailError('Configure your Gmail address and App Password in API Access > Email & Hosting settings.')
    sender = sender.strip()
    password = password.strip().replace(' ', '')
    recipient = company['email'].strip()
    if any('\r' in v or '\n' in v for v in (sender, recipient)):
        raise MailError('Check the sender and recipient email addresses.')

    message = EmailMessage()
    message['From'] = sender
    message['To'] = recipient
    message['Subject'] = 'Chintamani Gems - Inventory API access'
    message['Message-ID'] = __import__('email.utils', fromlist=['make_msgid']).make_msgid(domain='chintamanigems.local')
    message.set_content('Hello ' + company['name'] + ',\n\nYour read-only inventory API is ready.\n\nURL: ' + api_url + '\nHTTP method: GET\nAuthentication header: x-api-key\nAPI key: ' + key + '\nArray data key: diamonds\n\nInventory types: ' + ', '.join(json.loads(company['kinds'])) + '\n\nThe response is a full snapshot of the latest published inventory. Prices already include your configured adjustment. Please keep this key private. The integration guide is attached.\n\nChintamani Gems')
    message.add_attachment(pdf_bytes, maintype='application', subtype='pdf', filename='Chintamani-Gems-API-Guide.pdf')

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as server:
            server.login(sender, password)
            server.send_message(message)
        return message['Message-ID']
    except smtplib.SMTPAuthenticationError:
        raise MailError('Gmail rejected the App Password. Check credentials in Email & Hosting settings.') from None
    except smtplib.SMTPRecipientsRefused:
        raise MailError(f'Gmail rejected the recipient address {recipient}. Check the company email address.') from None
    except Exception as exc:
        raise MailError('Email outcome uncertain (' + type(exc).__name__ + '). Check Gmail Sent mail before resending; no automatic retry will occur.', True) from None


def connect_google(client_file):
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request
        config=json.loads(__import__('pathlib').Path(client_file).read_text(encoding='utf-8-sig'))
        installed=config.get('installed',{})
        if not installed.get('client_id') or not installed.get('auth_uri','').startswith('https://accounts.google.com/') or installed.get('token_uri')!='https://oauth2.googleapis.com/token':
            raise ValueError()
        flow=InstalledAppFlow.from_client_config(config,SCOPES,autogenerate_code_verifier=True)
        credentials=flow.run_local_server(host='127.0.0.1',port=0,open_browser=True,timeout_seconds=180,
                                          authorization_prompt_message='Complete Google sign-in in your browser.',success_message='Google account connected. Close this tab and return to Chintamani Gems.')
        claims=id_token.verify_oauth2_token(credentials.id_token,Request(),installed['client_id'])
        email=claims.get('email','')
        if not claims.get('email_verified') or not email:raise ValueError()
        portal_secrets.put_secret('google-credentials',credentials.to_json())
        portal_secrets.put_secret('google-sender',email)
        portal_secrets.put_secret('google-smtp-sender',email)
        return email
    except Exception as exc:
        raise MailError('Google sign-in could not finish ('+type(exc).__name__+'). Check the Desktop OAuth client, consent configuration and browser sign-in.') from None


def gmail_credentials():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        raw=portal_secrets.get_secret('google-credentials')
        if not raw:raise MailError('Connect your Google account in API Access > Email & Hosting settings.')
        credentials=Credentials.from_authorized_user_info(json.loads(raw),SCOPES)
        if not credentials.valid:
            if not credentials.refresh_token:raise ValueError()
            credentials.refresh(Request())
            portal_secrets.put_secret('google-credentials',credentials.to_json())
        return credentials
    except MailError:raise
    except Exception as exc:raise MailError('Google authorization is unavailable ('+type(exc).__name__+'). Reconnect the sender account.') from None


def send_invitation(company, key, api_url, pdf_bytes, http=None, credential_provider=None, sender=None, use_oauth=False):
    """
    Sends company invitation. Prefers SMTP App Password when configured,
    falling back to OAuth REST API if explicitly requested or if OAuth credentials exist and SMTP password is not set.
    """
    app_pw = portal_secrets.get_secret('google-smtp-password')
    if not use_oauth and not credential_provider and app_pw:
        return send_smtp_invitation(company, key, api_url, pdf_bytes, sender=sender, password=app_pw)

    # Fallback to OAuth if credentials provided or OAuth explicitly requested
    credentials=(credential_provider or gmail_credentials)()
    sender=sender or portal_secrets.get_secret('google-sender')
    recipient=company['email']
    if not sender or any('\r' in v or '\n' in v for v in (sender,recipient)):
        raise MailError('Check the sender and recipient email addresses.')
    message=EmailMessage();message['From']=sender;message['To']=recipient
    message['Subject']='Chintamani Gems - Inventory API access'
    message['Message-ID']=__import__('email.utils',fromlist=['make_msgid']).make_msgid(domain='chintamanigems.local')
    message.set_content('Hello '+company['name']+',\n\nYour read-only inventory API is ready.\n\nURL: '+api_url+'\nHTTP method: GET\nAuthentication header: x-api-key\nAPI key: '+key+'\nArray data key: diamonds\n\nInventory types: '+', '.join(json.loads(company['kinds']))+'\n\nThe response is a full snapshot of the latest published inventory. Prices already include your configured adjustment. Please keep this key private. The integration guide is attached.\n\nChintamani Gems')
    message.add_attachment(pdf_bytes,maintype='application',subtype='pdf',filename='Chintamani-Gems-API-Guide.pdf')
    owned=http is None;http=http or httpx.Client(timeout=45,follow_redirects=False)
    try:
        r=http.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',headers={'Authorization':'Bearer '+credentials.token},
                    json={'raw':base64.urlsafe_b64encode(message.as_bytes()).decode()})
        if not 200<=r.status_code<300:raise MailError('Gmail did not accept the email (HTTP '+str(r.status_code)+'). Verify the sender account before an explicit resend.')
        data=r.json()
        if not isinstance(data.get('id'),str) or not data['id']:raise MailError('Gmail returned an unverified send result. Check Sent mail before resending.',True)
        return data['id']
    except MailError:raise
    except Exception as exc:raise MailError('Email outcome uncertain ('+type(exc).__name__+'). Check Gmail Sent mail before resending; no automatic retry will occur.',True) from None
    finally:
        if owned:http.close()
