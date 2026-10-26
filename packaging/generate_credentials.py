"""Build-only: wrap current shared keys without writing plaintext credentials."""
from pathlib import Path
import base64
import json
import os
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from keyring.backends.Windows import WinVaultKeyring
from drc_sync.core import SERVICE

def main():
    try:
        vault = WinVaultKeyring()
        values = {kind:vault.get_password(SERVICE, kind) for kind in ('Natural','Lab Grown')}
        if any(not isinstance(v,str) or not v.strip() for v in values.values()):
            raise ValueError()
        wrapping = AESGCM.generate_key(bit_length=256); nonce = os.urandom(12)
        payload = AESGCM(wrapping).encrypt(nonce, json.dumps(values).encode(), b'ChintamaniGems:shared-credentials:v1')
        encoded = lambda value:repr(base64.b64encode(value).decode())
        source = '# Generated private build input. Never commit or display credential values.\n'
        source += 'WRAPPING_KEY = '+encoded(wrapping)+'\nNONCE = '+encoded(nonce)+'\nPAYLOAD = '+encoded(payload)+'\n'
        (Path(__file__).resolve().parent.parent/'drc_sync/_bundled_credentials.py').write_text(source)
    except Exception:
        raise SystemExit('Shared credential build failed. Check both saved inventory credentials in Windows Credential Manager.') from None
    print('Shared credential bundle prepared; credential values were not displayed.')

if __name__ == '__main__': main()
