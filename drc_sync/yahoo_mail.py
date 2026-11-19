"""Yahoo Mail SMTP invitations for the desktop inventory publisher."""
import json
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid


SMTP_HOST = 'smtp.mail.yahoo.com'
SMTP_PORT = 465


class MailError(ValueError):
    """A safe message for the operator, with the delivery outcome when known."""

    def __init__(self, detail, uncertain=False):
        super().__init__(detail)
        self.uncertain = uncertain


def validate_credentials(sender, password):
    sender = (sender or '').strip()
    password = (password or '').strip().replace(' ', '')
    if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', sender) or not password:
        raise MailError('Enter your full Yahoo email address and generated app password.')
    return sender, password


def test_smtp_auth(sender, password):
    """Check the Yahoo login without submitting any email."""
    sender, password = validate_credentials(sender, password)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=15) as server:
            server.login(sender, password)
    except smtplib.SMTPAuthenticationError:
        raise MailError('Yahoo Mail rejected the login. Check your email address and app password.') from None
    except (OSError, smtplib.SMTPException):
        raise MailError('Could not connect to Yahoo Mail SMTP. Check your network and try again.') from None


def invitation_email_text(company, api_url, key):
    kinds = ', '.join(json.loads(company['kinds']))
    feedback_url = api_url.replace('/inventory', '/feedback')
    return (
        f"Hello {company['name']},\n\n"
        "Your read-only inventory API is ready.\n\n"
        "----------------------------------------\n"
        "Step 1: Fetch Diamonds (GET)\n"
        "----------------------------------------\n"
        f"- Endpoint: {api_url}\n"
        "- HTTP Method: GET\n"
        "- Authentication Header: x-api-key\n"
        f"- API Key: {key}\n"
        f"- Inventory Scope: {kinds}\n\n"
        "Response Format:\n"
        "The inventory is returned as a JSON snapshot with two separate arrays:\n"
        '- "natural": [ ... ] (Natural diamonds)\n'
        '- "lab_grown": [ ... ] (Lab grown diamonds)\n\n'
        "Quick Fetch Test (cURL):\n"
        f'curl -X GET "{api_url}" -H "x-api-key: {key}"\n\n'
        "----------------------------------------\n"
        "Step 2: Sync Feedback Webhook (POST)\n"
        "----------------------------------------\n"
        "After fetching and importing into your website / ERP, please report your import summary:\n"
        f"- Endpoint: {feedback_url}\n"
        "- HTTP Method: POST\n"
        "- Authentication Header: x-api-key\n"
        "- Content-Type: application/json\n\n"
        "Feedback Payload Format:\n"
        '{\n'
        '  "success_count": 680,\n'
        '  "failed_count": 7,\n'
        '  "failures": [\n'
        '    {"stock_id": "NAT-1002", "reason": "Missing cut grade"},\n'
        '    {"stock_id": "LAB-405", "reason": "Invalid certificate URL"}\n'
        '  ],\n'
        '  "comment": "Imported to store"\n'
        '}\n\n'
        "Feedback cURL Example:\n"
        f'curl -X POST "{feedback_url}" \\\n'
        f'  -H "x-api-key: {key}" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        '  -d \'{"success_count":680,"failed_count":7,"failures":[{"stock_id":"NAT-1002","reason":"Missing cut grade"}]}\'' "\n\n" 
        "The complete integration guide is attached as a PDF.\n\n"
        "Chintamani Gems"
    )


def send_invitation(company, key, api_url, pdf_bytes, *, sender, password):
    sender, password = validate_credentials(sender, password)
    recipient = company['email'].strip()
    if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', recipient):
        raise MailError('Check the company email address.')

    message = EmailMessage()
    message['From'] = sender
    message['To'] = recipient
    message['Subject'] = 'Chintamani Gems - Inventory API access'
    message['Message-ID'] = make_msgid(domain='chintamanigems.local')
    message.set_content(invitation_email_text(company, api_url, key))
    message.add_attachment(pdf_bytes, maintype='application', subtype='pdf', filename='Chintamani-Gems-API-Guide.pdf')

    submitting = accepted = False
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=45) as server:
            server.login(sender, password)
            submitting = True
            server.send_message(message)
            accepted = True
    except smtplib.SMTPAuthenticationError:
        raise MailError('Yahoo Mail rejected the login. Check your email address and app password.') from None
    except smtplib.SMTPRecipientsRefused:
        raise MailError('Yahoo Mail rejected the recipient address. Check the company email address.') from None
    except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise MailError('Yahoo Mail rejected the invitation. Review the sender settings before resending.') from None
    except (OSError, smtplib.SMTPException):
        if not accepted:
            if submitting:
                raise MailError('Email outcome uncertain. Check Yahoo Sent mail before resending.', uncertain=True) from None
            raise MailError('Could not connect to Yahoo Mail SMTP. Check your network and try again.') from None
    return message['Message-ID']
