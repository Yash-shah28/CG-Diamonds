"""Use Windows Credential Manager explicitly; never fall back to plaintext."""
from keyring.backends.Windows import WinVaultKeyring
from .core import SERVICE

def read_key(kind):
    return WinVaultKeyring().get_password(SERVICE, kind)

def save_key(kind, value):
    WinVaultKeyring().set_password(SERVICE, kind, value)
