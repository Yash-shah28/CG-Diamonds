"""Owner sessions, local migration discovery, and the scoped workspace transport."""
import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import portal_secrets
from .backup import BACKUP_SETTING, MAX_BACKUP, has_local_data, publication_blocked
from .contract import new_profile
from .portal_client import HostingError, PortalClient, clean_base


SETTING = 'owner_workspace'
CLOUD = 'active'
PROVIDER_SLOTS = {'rapaport': 'rapaport-api', 'hrd': 'lab-report-hrd', 'email': 'yahoo-smtp-credentials'}
FEATURE_FOR_KIND = {'Natural': 'natural', 'Lab Grown': 'lab_grown'}


def token_slot(origin):
    return 'workspace-session:' + hashlib.sha256(origin.encode()).hexdigest()


def sign_in_origin(store):
    linked = configuration(store).get('origin')
    if linked:
        return clean_base(linked)
    origin = os.environ.get('CHINTAMANI_WORKSPACE_ORIGIN')
    if not origin:
        bundled = Path(__file__).with_name('assets') / 'owner-workspace-origin.txt'
        if bundled.is_file():
            origin = bundled.read_text(encoding='utf-8').strip()
    origin = origin or store.get('workspace_setup_origin', '')
    return clean_base(origin) if origin else ''


class WorkspaceClient:
    def __init__(self, origin, token=None, http=None, *, timeout=None):
        self.origin = clean_base(origin)
        self.token = token
        self.http = http or httpx.Client(timeout=timeout or httpx.Timeout(60, connect=15), follow_redirects=False)
        self.owned = http is None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.owned:
            self.http.close()

    def call(self, method, route, data=None):
        headers = {'Authorization': 'Bearer ' + self.token} if self.token else {}
        try:
            response = self.http.request(method, self.origin + route, headers=headers, json=data)
        except httpx.RequestError:
            raise HostingError('The workspace service could not be reached.') from None
        if response.status_code == 204:
            return None
        try:
            result = response.json()
        except ValueError:
            raise HostingError('The workspace returned an unreadable response.') from None
        if not isinstance(result, dict):
            raise HostingError('The workspace returned an invalid response.')
        if not response.is_success:
            detail = result.get('error')
            error = HostingError(detail if isinstance(detail, str) else 'Workspace request failed.',
                                 response.status_code >= 500 or response.status_code == 429)
            error.http_status = response.status_code
            raise error
        return result

    def sign_in(self, login, password):
        result = self.call('POST', '/owner/sign-in', {'login': login, 'password': password})
        self.token = result['token']
        return result['must_change_password']

    def change_password(self, current, new):
        self.token = self.call('POST', '/owner/password', {
            'current_password': current, 'password': new,
        })['token']

    def bootstrap(self):
        return self.call('GET', '/owner/bootstrap')

    def upload_backup(self, payload, checksum, device, revision, *, direct=False, workspace_id=None):
        if direct:
            return self.upload_backup_direct(payload, checksum, device, revision, workspace_id)
        headers = {
            'Authorization': 'Bearer ' + self.token,
            'Content-Type': 'application/zip',
            'X-Backup-Sha256': checksum,
            'X-Backup-Device': device,
            'X-Backup-Revision': str(revision),
        }
        try:
            response = self.http.put(self.origin + '/owner/backup', headers=headers, content=payload)
            if response.status_code == 409:
                raise HostingError('The workspace backup changed. Backup will check again.', True)
            response.raise_for_status()
            return response.json()['backup']
        except (httpx.HTTPError, ValueError, KeyError):
            raise HostingError('Could not save the workspace backup. Backup will retry.') from None

    def upload_backup_direct(self, payload, checksum, device, revision, workspace_id):
        grant = self.call('POST', '/owner/backup/prepare', {
            'digest': checksum, 'size': len(payload), 'device': device, 'revision': revision,
        })
        if 'backup' in grant:
            return grant['backup']
        try:
            key = base64.b64decode(grant['encryption'], validate=True)
            nonce = os.urandom(12)
            encrypted = AESGCM(key).encrypt(nonce, payload, (workspace_id + ':' + checksum).encode())
            body = nonce + encrypted[-16:] + encrypted[:-16]
            response = self.http.put(trusted_signed_url(grant['upload_url']), content=body,
                                     headers={'Content-Type': 'application/octet-stream'})
            response.raise_for_status()
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise HostingError('Could not send the encrypted backup to private storage. Backup will retry.') from None
        return self.call('POST', '/owner/backup/finish', {'ticket': grant['ticket']})['backup']

    def download_backup(self, expected, *, direct=False, workspace_id=None):
        capacity = expected['size']
        if type(capacity) is not int or not 4 <= capacity <= MAX_BACKUP:
            raise HostingError('The saved backup has an invalid size.', False)
        if direct or expected.get('direct'):
            grant = self.call('GET', '/owner/backup/grant')
            if any(grant.get(field) != expected[field] for field in ('size', 'revision', 'sha256')):
                raise HostingError('The saved backup has changed. Restore will retry.')
            cipher = self.read_backup(stream_url=trusted_signed_url(grant['download_url']), capacity=capacity + 28)
            try:
                key = base64.b64decode(grant['encryption'], validate=True)
                payload = AESGCM(key).decrypt(cipher[:12], cipher[28:] + cipher[12:28],
                                              (workspace_id + ':' + expected['sha256']).encode())
            except (KeyError, ValueError, InvalidTag, TypeError):
                raise HostingError('The encrypted backup could not be verified. Restore will retry.') from None
        else:
            payload = self.read_backup(stream_url=self.origin + '/owner/backup/download', capacity=capacity,
                                       headers={'Authorization': 'Bearer ' + self.token})
        if len(payload) != capacity or hashlib.sha256(payload).hexdigest() != expected['sha256']:
            raise HostingError('The workspace backup could not be verified. Restore will retry.')
        return payload

    def read_backup(self, *, stream_url, capacity, headers=None):
        data = bytearray()
        try:
            with self.http.stream('GET', stream_url, headers=headers) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > capacity:
                        break
        except httpx.HTTPError:
            raise HostingError('Could not download the workspace backup. Restore will retry.') from None
        if len(data) != capacity:
            raise HostingError('The saved backup has an invalid size. Restore will retry.')
        return bytes(data)

    def integration(self, provider, operation, *, request_id=None, **inputs):
        return self.call('POST', '/owner/integrations', {
            'provider': provider, 'operation': operation,
            'request_id': str(request_id or uuid4()), **inputs,
        })

    def upload_rapaport(self, content):
        identifier = uuid4()
        prepared = self.integration('rapaport', 'prepare', request_id=identifier, size=len(content))
        url = trusted_signed_url(prepared['upload_url'])
        try:
            response = self.http.put(url, content=content, headers={'Content-Type': 'application/json'}, timeout=120)
            response.raise_for_status()
        except httpx.HTTPError:
            raise HostingError('Could not stage the Natural inventory for upload.') from None
        return self.integration('rapaport', 'upload', request_id=identifier, size=len(content),
                                sha256=hashlib.sha256(content).hexdigest())['uploadId']


def trusted_signed_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment:
        raise HostingError('The workspace returned an invalid private storage link.', False)
    return value


def configuration(store):
    return store.get(SETTING, {})


def workspace_context(store):
    info = configuration(store)
    if not info.get('id'):
        return None
    return info


def feature_enabled(store, name):
    info = workspace_context(store)
    return not info or name in info.get('features', [])


def connection_for(info, http=None):
    token = portal_secrets.get_secret(token_slot(info['origin']))
    if not token:
        raise HostingError('Sign in to your company workspace.')
    return WorkspaceClient(info['origin'], token, http=http)


def cloud_workspace(store):
    info = workspace_context(store)
    return info if info and info.get(CLOUD) else None


def provider_workspace(store, provider):
    info = workspace_context(store)
    return info if info and (info.get(CLOUD) or provider in info.get('providers', [])) else None


def hosting_settings(store):
    info = cloud_workspace(store)
    existing = store.get('portal_hosting', {})
    if publication_blocked(store):
        return {**existing, 'backup_replica': True}
    if info:
        return {**existing, 'owner_workspace': info}
    return existing


class ScopedPortalClient(PortalClient):
    def __init__(self, settings, *, http=None):
        self.workspace = connection_for(settings['owner_workspace'], http=http)
        self.base = self.workspace.origin
        self.http = self.workspace.http

    def close(self):
        self.workspace.__exit__()

    def admin(self, payload=None):
        return self.workspace.call('POST' if payload else 'GET', '/owner/inventory-admin', payload)

    def register_buyer(self, company, previous_key_hash=None):
        data = {key: company[key] for key in ('id', 'key_hash', 'name', 'email', 'mode', 'percent', 'active')}
        data['kinds'] = json.loads(company['kinds'])
        if previous_key_hash:
            data['previous_key_hash'] = previous_key_hash
        return self.workspace.call('PUT', '/owner/buyers', data)

    def publish(self, job, company_name=None):
        raw = job['payload'].encode()
        payload = json.loads(raw)
        records = len(payload['diamonds']) if 'diamonds' in payload else len(payload.get('natural', [])) + len(payload.get('lab_grown', []))
        metadata = {
            'client_id': job['client_id'], 'revision': job['revision'], 'key_hash': job['key_hash'],
            'kinds': json.loads(job['kinds']), 'sha256': hashlib.sha256(raw).hexdigest(),
            'size': len(raw), 'records': records, 'company_name': company_name,
        }
        authorization = self.admin({'action': 'authorize', **metadata})
        if not authorization.get('already_published'):
            headers = {'Content-Type': 'application/json'}
            if authorization.get('upload_if_absent'):
                headers['If-None-Match'] = '*'
            try:
                response = self.http.put(trusted_signed_url(authorization['upload_url']), content=raw,
                                         headers=headers, timeout=120)
                if response.status_code not in (409, 412):
                    response.raise_for_status()
            except httpx.HTTPError:
                raise HostingError('Private inventory upload failed. Retry when storage is available.') from None
        return self.admin({'action': 'commit', **metadata})


def portal_client(settings, legacy_factory=PortalClient):
    if settings.get('backup_replica'):
        raise HostingError('Publishing stays on the primary PC until the workspace is handed off.', False)
    return ScopedPortalClient(settings) if settings.get('owner_workspace') else legacy_factory(settings)


def migration_package(store, features, migration_id):
    profiles = [profile for profile in store.get('profiles', []) if FEATURE_FOR_KIND.get(profile.get('kind')) in features]
    buyers = []
    if 'buyer_feeds' in features:
        for company in store.companies():
            buyer = {name: company[name] for name in
                     ('id', 'name', 'email', 'mode', 'percent', 'key_hash', 'revision', 'published', 'active')}
            buyer['kinds'] = json.loads(company['kinds'])
            buyers.append(buyer)
    credentials = {}
    for provider, slot in PROVIDER_SLOTS.items():
        if provider not in features:
            continue
        stored = portal_secrets.get_secret(slot)
        if not stored:
            continue
        data = json.loads(stored)
        if provider == 'hrd':
            data = {'token': data['key']}
        credentials[provider] = data
    return {'migration_id': migration_id, 'profiles': profiles, 'buyers': buyers, 'credentials': credentials}


def remember_workspace(store, client, info, *, login=None):
    previous = configuration(store)
    if previous.get('id') and (previous['id'] != info['id'] or previous['origin'] != client.origin):
        raise HostingError('This local inventory is already linked to a different workspace.', False)
    portal_secrets.put_secret(token_slot(client.origin), client.token)
    updated = {**previous, **{field: info[field] for field in ('id', 'name', 'features', 'providers', 'migrated', 'publishing_enabled', 'feed_url')},
               'origin': client.origin, 'backup_enabled': bool(info.get('backup_enabled')),
               'legacy_import': bool(info.get('legacy_import')), 'provider_refs': info.get('provider_refs', {})}
    if login:
        updated['login'] = login
    if not previous.get('id'):
        if updated['backup_enabled'] and not has_local_data(store):
            store.set(BACKUP_SETTING, {'restore_pending': True, 'status': 'Checking for a saved workspace.'})
        elif info.get('migrated'):
            restore_workspace_configuration(store, info)
    store.set(SETTING, updated)
    return updated


def restore_workspace_configuration(store, info):
    if not store.get('profiles', []):
        profiles = info.get('settings', {}).get('profiles', [])
        store.set('profiles', [{**new_profile(), **profile} for profile in profiles])
    with store.db:
        for buyer in info.get('buyers', []):
            settings = buyer['settings']
            published = settings.get('records', 0)
            status = 'Published' if published and settings['active'] else 'Draft' if settings['active'] else 'Revoked'
            store.db.execute('''
                INSERT OR IGNORE INTO portal_companies
                (id,name,email,kinds,mode,percent,revision,key_hash,active,status,published)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ''', (buyer['id'], settings['name'], settings['email'], json.dumps(settings['kinds']),
                  settings['mode'], settings['percent'], settings.get('verified_revision', 0), buyer['key_hash'],
                  int(settings['active']), status, published))
