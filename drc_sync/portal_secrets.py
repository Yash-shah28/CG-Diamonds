"""Separate Windows vault slots for publishing, company keys and Yahoo Mail credentials."""
import json
import re
from keyring.backends.Windows import WinVaultKeyring
SERVICE = 'Chintamani Gems Portal'

def get_secret(slot):
    return WinVaultKeyring().get_password(SERVICE, slot)

def put_secret(slot, value):
    WinVaultKeyring().set_password(SERVICE, slot, value)

def company_slot(client_id, key_hash):
    if not re.fullmatch('[a-f0-9]{32}', client_id) or not re.fullmatch('[a-f0-9]{64}', key_hash):
        raise ValueError('Invalid company credential reference')
    return 'company:'+client_id+':'+key_hash

def company_key(client_id, key_hash):
    return get_secret(company_slot(client_id, key_hash))

def hosting_slot(origin):
    import hashlib
    return 'hosting-admin:'+hashlib.sha256(origin.encode()).hexdigest()

def get_smtp_credentials():
    raw = get_secret('yahoo-smtp-credentials')
    if not raw:
        return None, None
    try:
        credentials = json.loads(raw)
        return credentials['sender'], credentials['password']
    except (ValueError, TypeError, KeyError):
        return None, None

def put_smtp_credentials(sender, password):
    put_secret('yahoo-smtp-credentials', json.dumps({'sender': sender, 'password': password}))
