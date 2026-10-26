"""Windows Credential Manager storage with installer-supplied shared credentials.

The bundled payload prevents casual disclosure, not extraction by a local admin.
No credentials are exposed through the app UI, logs or a plaintext config file.
"""
import base64
import json
import sys
from functools import lru_cache
from types import MappingProxyType
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from keyring.backends.Windows import WinVaultKeyring
from .core import SERVICE

KINDS = ('Natural', 'Lab Grown')

class CredentialProvisionError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def shared_keys():
    try:
        from ._bundled_credentials import WRAPPING_KEY, NONCE, PAYLOAD
    except ImportError:
        if getattr(sys, 'frozen', False):
            raise CredentialProvisionError('Shared credential setup is unavailable.') from None
        return None
    try:
        body = AESGCM(base64.b64decode(WRAPPING_KEY)).decrypt(
            base64.b64decode(NONCE), base64.b64decode(PAYLOAD), b'ChintamaniGems:shared-credentials:v1')
        values = json.loads(body)
        if set(values) != set(KINDS) or any(not isinstance(values[k], str) or not values[k].strip() for k in KINDS):
            raise ValueError()
        return MappingProxyType(values)
    except Exception:
        raise CredentialProvisionError('Shared credential setup could not be verified.') from None


def provision_shared_credentials(vault=None):
    keys = shared_keys()
    if keys is None:
        return False
    vault = vault or WinVaultKeyring()
    try:
        for kind in KINDS:
            if vault.get_password(SERVICE, kind) != keys[kind]:
                vault.set_password(SERVICE, kind, keys[kind])
        return True
    except Exception:
        raise CredentialProvisionError('Windows Credential Manager could not store the shared credentials.') from None


def read_key(kind):
    if kind not in KINDS:
        raise CredentialProvisionError('Unknown inventory type.')
    provision_shared_credentials()
    return WinVaultKeyring().get_password(SERVICE, kind)


def save_key(kind, value):
    keys = shared_keys()
    if keys is not None:
        raise CredentialProvisionError('API credentials are managed by the installer.')
    WinVaultKeyring().set_password(SERVICE, kind, value)


def credential_self_test():
    """Exercise fresh-machine provisioning entirely in memory, without requests."""
    class MemoryVault:
        def __init__(self): self.values = {}; self.writes = 0
        def get_password(self, service, kind): return self.values.get((service, kind))
        def set_password(self, service, kind, value): self.values[service, kind] = value; self.writes += 1
    try:
        vault = MemoryVault()
        first = provision_shared_credentials(vault)
        second = provision_shared_credentials(vault)
        ok = first and second and vault.writes == 2 and all(vault.get_password(SERVICE, kind) for kind in KINDS)
        return {'ok':bool(ok), 'offline':True, 'requests_sent':0, 'inventory_types':list(KINDS), 'idempotent':vault.writes == 2}
    except Exception as exc:
        return {'ok':False, 'offline':True, 'requests_sent':0, 'error_type':type(exc).__name__}
