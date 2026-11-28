import base64
import hashlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from drc_sync.portal_client import HostingError
from drc_sync.portal_store import PortalStore
from drc_sync.updater import UpdateError, verify_manifest
from drc_sync.workspace import ScopedPortalClient, WorkspaceClient, migration_package, remember_workspace


def test_workspace_login_password_change_and_migration_excludes_raw_buyer_key(tmp_path, monkeypatch):
    store = PortalStore(tmp_path / 'owner.db')
    try:
        cid = store.add_company('Buyer', 'buyer@example.test', ['Natural'], secret_writer=lambda *args: None)
        secrets = {'lab-report-hrd': '{"key":"fixture-hrd"}'}
        monkeypatch.setattr('drc_sync.workspace.portal_secrets.get_secret', secrets.get)
        monkeypatch.setattr('drc_sync.workspace.portal_secrets.put_secret', secrets.__setitem__)
        workspace = {'id': 'workspace-one', 'name': 'Owner', 'features': ['natural','buyer_feeds','hrd'],
                     'providers': [], 'migrated': False, 'publishing_enabled': False,
                     'feed_url': 'https://legacy.example.test/api/v1/inventory'}
        operations = []

        def request(req):
            operations.append(req.url.path)
            if req.url.path == '/owner/sign-in':
                assert json.loads(req.content) == {'login': 'owner', 'password': 'temporary'}
                return httpx.Response(200, json={'must_change_password': True, 'token': 'temporary-token'})
            if req.url.path == '/owner/password':
                assert req.headers['Authorization'] == 'Bearer temporary-token'
                return httpx.Response(200, json={'token': 'replacement-token'})
            assert req.headers['Authorization'] == 'Bearer replacement-token'
            return httpx.Response(200, json=workspace)

        with httpx.Client(transport=httpx.MockTransport(request)) as http:
            with WorkspaceClient('https://owner.example.test', http=http) as client:
                assert client.sign_in('owner', 'temporary')
                client.change_password('temporary', 'private-password')
                remember_workspace(store, client, client.bootstrap())
                assert operations == ['/owner/sign-in', '/owner/password', '/owner/bootstrap']
                assert 'replacement-token' not in json.dumps(store.get('owner_workspace', {}))
                with pytest.raises(HostingError, match='different workspace'):
                    remember_workspace(store, client, {**workspace, 'id': 'workspace-two'})
        package = migration_package(store, workspace['features'], 'fixture-migration')
        assert package['buyers'][0]['id'] == cid
        assert package['buyers'][0]['key_hash'] == store.company(cid)['key_hash']
        assert 'cg_read_' not in json.dumps(package)
        assert package['credentials'] == {'hrd': {'token': 'fixture-hrd'}}
    finally:
        store.db.close()


@pytest.mark.parametrize(('replace_all', 'submission'), [(False, 'upload'), (True, 'replace')])
def test_hosted_rapaport_dispatches_the_selected_upload_mode(replace_all, submission):
    content = b'Stock #,Availability,Shape,Weight,PricePerCarat\r\n1,G,Round,1,100\r\n'
    operations = []
    request_id = None

    def request(req):
        nonlocal request_id
        if req.method == 'PUT':
            assert req.url.host == 'files.example.test'
            assert req.content == content
            return httpx.Response(200)
        assert req.url.path == '/owner/integrations'
        data = json.loads(req.content)
        assert data['provider'] == 'rapaport'
        assert data['size'] == len(content)
        operations.append(data['operation'])
        if data['operation'] == 'prepare':
            request_id = data['request_id']
            return httpx.Response(200, json={'upload_url': 'https://files.example.test/inventory.csv'})
        assert data['request_id'] == request_id
        assert data['sha256'] == hashlib.sha256(content).hexdigest()
        return httpx.Response(200, json={'uploadId': 'fixture-upload'})

    with httpx.Client(transport=httpx.MockTransport(request)) as http:
        with WorkspaceClient('https://owner.example.test', token='fixture', http=http) as client:
            assert client.upload_rapaport(content, replace_all=replace_all) == 'fixture-upload'
    assert operations == ['prepare', submission]


def test_private_manifest_requires_signature_and_matching_object_key():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    installer = b'fake exe'
    metadata = {'schema': 2, 'app': 'chintamani-gems', 'platform': 'windows-x64', 'version': '0.9.0',
                'size': len(installer), 'sha256': hashlib.sha256(installer).hexdigest(),
                'object_key': 'private-releases/0.9.0/ChintamaniGemsSetup-0.9.0.exe'}

    def envelope(metadata):
        body = json.dumps(metadata).encode()
        return {'payload': base64.b64encode(body).decode(), 'signature': base64.b64encode(key.sign(body)).decode()}

    assert verify_manifest(envelope(metadata), public, private=True) == metadata
    with pytest.raises(UpdateError):
        verify_manifest(envelope({**metadata, 'object_key': 'private-releases/other.exe'}), public, private=True)
    with pytest.raises(UpdateError):
        verify_manifest(envelope(metadata), public)


def test_first_sign_in_on_another_pc_restores_mapping_and_buyer_identity(tmp_path, monkeypatch):
    store = PortalStore(tmp_path / 'replacement-pc.db')
    secret_store = {}
    monkeypatch.setattr('drc_sync.workspace.portal_secrets.put_secret', secret_store.__setitem__)
    buyer_hash = hashlib.sha256(b'fixture-key').hexdigest()
    workspace = {
        'id': 'workspace-one', 'name': 'Owner', 'features': ['natural', 'buyer_feeds'],
        'providers': [], 'migrated': True, 'publishing_enabled': True,
        'feed_url': 'https://legacy.example.test/api/v1/inventory',
        'settings': {'profiles': [{'id': 'natural-main', 'kind': 'Natural', 'mapping': {'Stock Number': 'StockId'}}]},
        'buyers': [{'id': 'old-buyer-id', 'key_hash': buyer_hash, 'settings': {
            'name': 'Buyer', 'email': 'buyer@example.test', 'kinds': ['Natural'], 'mode': 'discount',
            'percent': '4.5', 'active': True, 'verified_revision': 7, 'records': 24,
        }}],
    }
    try:
        with WorkspaceClient('https://owner.example.test', token='fixture-session') as client:
            remember_workspace(store, client, workspace)
            assert store.get('profiles', [])[0]['mapping'] == {'Stock Number': 'StockId'}
            buyer = store.company('old-buyer-id')
            assert (buyer['key_hash'], buyer['revision'], buyer['published']) == (buyer_hash, 7, 24)
            assert buyer['status'] == 'Published'
            assert list(secret_store.values()) == ['fixture-session']
    finally:
        store.db.close()


def test_portable_publish_retries_existing_upload_only_through_verified_commit(monkeypatch):
    operations = []
    monkeypatch.setattr('drc_sync.workspace.portal_secrets.get_secret', lambda slot: 'fixture-token')

    def request(req):
        if req.method == 'PUT':
            assert req.headers['If-None-Match'] == '*'
            operations.append('create-only upload')
            return httpx.Response(412)
        data = json.loads(req.content)
        operations.append(data['action'])
        if data['action'] == 'authorize':
            return httpx.Response(200, json={'upload_if_absent': True, 'upload_url': 'https://storage.example.test/snapshot'})
        assert data['sha256'] == hashlib.sha256(b'{"diamonds":[]}').hexdigest()
        return httpx.Response(200, json={'ok': True})

    with httpx.Client(transport=httpx.MockTransport(request)) as http:
        client = ScopedPortalClient({'owner_workspace': {'origin': 'https://owner.example.test'}}, http=http)
        job = {'client_id': 'fixture-buyer', 'revision': 12, 'key_hash': 'hash', 'kinds': '["Natural"]', 'payload': '{"diamonds":[]}'}
        assert client.publish(job) == {'ok': True}
    assert operations == ['authorize', 'create-only upload', 'commit']


def test_direct_backup_encrypts_storage_upload_and_restores_verified_data():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    workspace_id = 'workspace'
    payload = b'PK\x03\x04sample backup'
    digest = hashlib.sha256(payload).hexdigest()
    metadata = {'sha256': digest, 'size': len(payload), 'revision': 1, 'direct': True}
    key = bytes(range(32))
    encoded_key = base64.b64encode(key).decode()
    aad = (workspace_id + ':' + digest).encode()
    encrypted = []

    def request(req):
        if req.url.host == 'storage.example.test':
            assert 'authorization' not in req.headers
            if req.method == 'PUT':
                body = req.content
                assert body != payload
                assert AESGCM(key).decrypt(body[:12], body[28:] + body[12:28], aad) == payload
                encrypted.append(body)
                return httpx.Response(200)
            return httpx.Response(200, content=encrypted[0])
        assert req.headers['authorization'] == 'Bearer owner-session'
        if req.url.path.endswith('/prepare'):
            return httpx.Response(200, json={'encryption': encoded_key, 'ticket': {'test': True},
                                            'upload_url': 'https://storage.example.test/upload'})
        if req.url.path.endswith('/finish'):
            assert encrypted
            return httpx.Response(200, json={'backup': metadata})
        return httpx.Response(200, json={**metadata, 'encryption': encoded_key,
                                        'download_url': 'https://storage.example.test/download'})

    with httpx.Client(transport=httpx.MockTransport(request)) as http:
        client = WorkspaceClient('https://owner.example.test', token='owner-session', http=http)
        assert client.upload_backup(payload, digest, 'device', 0, direct=True, workspace_id=workspace_id) == metadata
        assert client.download_backup(metadata, direct=True, workspace_id=workspace_id) == payload
