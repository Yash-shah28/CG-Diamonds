import smtplib
from io import BytesIO

import pytest
from pypdf import PdfReader

from drc_sync import portal_secrets, yahoo_mail
from drc_sync.integration_guide import guide_pdf


COMPANY = {'name': 'Example', 'email': 'dev@example.com', 'kinds': '["Natural"]'}
API_URL = 'https://example.com/api/v1/inventory'


@pytest.fixture
def smtp(monkeypatch):
    server = type('SMTPFixture', (), {'logins': [], 'messages': [], 'delivery_error': None})()

    class MockSMTP:
        def __init__(self, host, port, *, context, timeout):
            assert (host, port) == ('smtp.mail.yahoo.com', 465)
            assert context.check_hostname and timeout > 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, user, password):
            server.logins.append((user, password))
            if password == 'bad-password':
                raise smtplib.SMTPAuthenticationError(535, b'bad-password')

        def send_message(self, message):
            server.messages.append(message)
            if server.delivery_error:
                raise server.delivery_error

    monkeypatch.setattr(smtplib, 'SMTP_SSL', MockSMTP)
    return server


def test_connection_check_authenticates_without_email(smtp):
    yahoo_mail.test_smtp_auth('owner@yahoo.com', 'abcd efgh')
    assert smtp.logins == [('owner@yahoo.com', 'abcdefgh')]
    assert smtp.messages == []


def send(password='app-password'):
    return yahoo_mail.send_invitation(
        COMPANY, 'cg_read_TEST', API_URL, b'%PDF-1.4 fake',
        sender='owner@yahoo.com', password=password,
    )


def test_invitation_contains_key_and_pdf(smtp):
    message_id = send()
    message, = smtp.messages
    assert message['Message-ID'] == message_id
    assert message['From'] == 'owner@yahoo.com' and message['To'] == COMPANY['email']
    assert 'cg_read_TEST' in message.get_body().get_content()
    assert next(message.iter_attachments()).get_filename() == 'Chintamani-Gems-API-Guide.pdf'


def test_bad_login_is_definite_failure_without_secret(smtp):
    with pytest.raises(yahoo_mail.MailError, match='rejected the login') as exc:
        send('bad-password')
    assert not exc.value.uncertain and 'bad-password' not in str(exc.value)
    assert smtp.messages == []


def test_interrupted_delivery_is_uncertain_and_attempted_once(smtp):
    smtp.delivery_error = TimeoutError('server diagnostic contains a password')
    with pytest.raises(yahoo_mail.MailError, match='Check Yahoo Sent') as exc:
        send()
    assert exc.value.uncertain
    assert 'server diagnostic' not in str(exc.value)
    assert len(smtp.messages) == 1


def test_smtp_rejection_is_definite_failure(smtp):
    smtp.delivery_error = smtplib.SMTPDataError(554, b'private server diagnostic')
    with pytest.raises(yahoo_mail.MailError, match='rejected the invitation') as exc:
        send()
    assert not exc.value.uncertain and 'private server' not in str(exc.value)


def test_yahoo_credentials_use_own_vault_slot(monkeypatch):
    vault = {'google-smtp-sender': 'owner@gmail.com', 'google-smtp-password': 'old-password'}
    monkeypatch.setattr(portal_secrets, 'get_secret', vault.get)
    monkeypatch.setattr(portal_secrets, 'put_secret', vault.__setitem__)
    assert portal_secrets.get_smtp_credentials() == (None, None)
    portal_secrets.put_smtp_credentials('owner@yahoo.com', 'app-password')
    assert portal_secrets.get_smtp_credentials() == ('owner@yahoo.com', 'app-password')
    assert vault['google-smtp-password'] == 'old-password'


def test_pdf_documents_feed_and_uses_a_key_placeholder():
    pages = PdfReader(BytesIO(guide_pdf(API_URL, ['Natural', 'Lab Grown']))).pages
    text = '\n'.join(page.extract_text() for page in pages)
    assert 'YOUR_API_KEY' in text and 'cg_read_' not in text
    assert 'NATURAL' in text and 'LAB_GROWN' in text
    assert '59.50' in text and '"Image": "https://example.com/diamond.jpg"' in text and '"Clarity": ""' in text
