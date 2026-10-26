import pytest
from drc_sync import credentials as c

class Vault:
    def __init__(self):self.values={};self.writes=[]
    def get_password(self,service,kind):return self.values.get(kind)
    def set_password(self,service,kind,value):self.values[kind]=value;self.writes.append(kind)

@pytest.fixture
def vault(monkeypatch):
    v=Vault();monkeypatch.setattr(c,'WinVaultKeyring',lambda:v)
    monkeypatch.setattr(c,'shared_keys',lambda:{'Natural':'natural-test-secret','Lab Grown':'lab-test-secret'})
    return v

def test_fresh_machine_gets_both_shared_keys(vault):
    assert c.provision_shared_credentials()
    assert c.read_key('Natural')=='natural-test-secret'
    assert c.read_key('Lab Grown')=='lab-test-secret'
    assert vault.writes==['Natural','Lab Grown']

def test_existing_keys_are_consistent_with_shared_install(vault):
    vault.values.update({'Natural':'different','Lab Grown':'lab-test-secret'})
    c.provision_shared_credentials()
    assert vault.writes==['Natural']
    assert vault.values['Natural']=='natural-test-secret'

def test_no_user_override_of_managed_credentials(vault):
    with pytest.raises(c.CredentialProvisionError):c.save_key('Natural','override')
    assert vault.writes==[]

def test_credential_failure_has_no_plaintext_fallback(vault):
    def failed(*args):raise RuntimeError('natural-test-secret')
    vault.set_password=failed
    with pytest.raises(c.CredentialProvisionError) as exc:c.read_key('Natural')
    assert 'natural-test-secret' not in str(exc.value)

def test_old_development_storage_stays_supported(vault,monkeypatch):
    monkeypatch.setattr(c,'shared_keys',lambda:None)
    c.save_key('Natural','saved')
    assert c.read_key('Natural')=='saved'

def test_self_test_does_not_read_or_write_real_vault(vault,monkeypatch):
    monkeypatch.setattr(c,'WinVaultKeyring',lambda:pytest.fail('Real vault used'))
    result=c.credential_self_test()
    assert result['ok'] and result['idempotent'] and result['requests_sent']==0
    assert 'secret' not in str(result)

def test_unrecognized_type_blocks(vault):
    with pytest.raises(c.CredentialProvisionError):c.read_key('Other')

def test_corrupt_bundle_cannot_fall_back(monkeypatch,vault):
    def corrupt():raise c.CredentialProvisionError('Shared credential setup could not be verified.')
    monkeypatch.setattr(c,'shared_keys',corrupt)
    vault.values['Natural']='old'
    with pytest.raises(c.CredentialProvisionError):c.read_key('Natural')
    assert vault.writes==[]
