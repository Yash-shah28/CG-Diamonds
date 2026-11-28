"""Back up app data and connected Excel files for an authorized workspace."""
import hashlib
import io
import json
from os import fstat
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

import httpx

from .portal_client import HostingError

MAX_BACKUP = 64 * 1024 * 1024
BACKUP_SETTING = 'workspace_backup'
TABLES = {
    'errors': 'id', 'jobs': 'id', 'responses': 'id', 'portal_companies': 'id',
    'portal_history': 'id', 'portal_cache': 'kind', 'portal_auth_audit': 'request_id', 'portal_jobs': 'id',
}
SETTINGS = ('profiles', 'website_rates', 'ui.sidebar_collapsed', 'rapaport.last_upload')
WORKBOOKS = {'Natural': 'natural', 'Lab Grown': 'lab-grown'}


def has_local_data(store):
    return bool(store.get('profiles', []) or any(
        store.db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() for table in TABLES))


def publication_blocked(store):
    state = store.get(BACKUP_SETTING, {})
    return bool(state.get('replica') or state.get('restore_pending'))


def add_entry(archive, name, data):
    metadata = ZipInfo(name)
    metadata.compress_type = ZIP_DEFLATED
    archive.writestr(metadata, data)


def workbook_entry(profile, extension):
    if profile.get('kind') not in WORKBOOKS or extension not in ('.xlsx', '.xlsm'):
        raise HostingError('Backups support connected Excel .xlsx and .xlsm workbooks.', False)
    return WORKBOOKS[profile['kind']] + extension


def verify_workbook(data):
    try:
        with ZipFile(io.BytesIO(data)) as workbook:
            workbook.getinfo('[Content_Types].xml')
            workbook.getinfo('xl/workbook.xml')
    except (KeyError, BadZipFile):
        raise HostingError('A connected workbook is incomplete or invalid. Backup will retry.') from None


def make_backup(store):
    # A read transaction gives the archive a consistent view while the monitor runs.
    store.db.execute('BEGIN')
    try:
        settings = {key: store.get(key, None) for key in SETTINGS}
        tables = {table: [dict(row) for row in store.db.execute(f'SELECT * FROM {table} ORDER BY {column}')]
                  for table, column in TABLES.items()}
    finally:
        store.db.rollback()
    buffer = io.BytesIO()
    total = 0
    entries = {}
    with ZipFile(buffer, 'w') as archive:
        for profile in settings['profiles'] or []:
            if not profile.get('path'):
                continue
            path = Path(profile['path'])
            entry = workbook_entry(profile, path.suffix.lower())
            try:
                with path.open('rb') as workbook:
                    before = fstat(workbook.fileno())
                    payload = workbook.read(MAX_BACKUP - total + 1)
                    after = fstat(workbook.fileno())
            except OSError:
                raise HostingError('A connected Excel workbook is unavailable. Backup will retry.') from None
            total += len(payload)
            if total >= MAX_BACKUP:
                raise HostingError('The connected workbooks exceed the 64 MiB backup limit.', False)
            if before.st_mtime_ns != after.st_mtime_ns or before.st_size != len(payload):
                raise HostingError('A workbook changed while being backed up. Backup will retry.')
            verify_workbook(payload)
            entries[profile['kind']] = entry
            add_entry(archive, entry, payload)
        manifest = {'format': 1, 'settings': settings, 'tables': tables, 'workbooks': entries}
        encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if total + len(encoded) > MAX_BACKUP:
            raise HostingError('The app data and workbooks exceed the 64 MiB backup limit.', False)
        add_entry(archive, 'workspace.json', encoded)
    if buffer.tell() > MAX_BACKUP:
        raise HostingError('The app backup exceeds the 64 MiB backup limit.', False)
    return buffer.getvalue()


def restore_backup(store, folder, payload, revision):
    if has_local_data(store):
        raise HostingError('This PC already has app data. Its files were kept.', False)
    output = folder / 'restored-workbooks' / str(revision)
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > 3 or sum(entry.file_size for entry in entries) > MAX_BACKUP:
                raise HostingError('Backup exceeds the restore limit.', False)
            manifest = json.loads(archive.read('workspace.json'))
            if manifest['format'] != 1 or set(manifest['settings']) != set(SETTINGS) or set(manifest['tables']) != set(TABLES):
                raise HostingError('This backup format cannot be opened.', False)
            expected = {'workspace.json'}
            workbooks = []
            for profile in manifest['settings']['profiles'] or []:
                if not profile.get('path'):
                    continue
                entry = workbook_entry(profile, Path(profile['path']).suffix.lower())
                if manifest['workbooks'].get(profile['kind']) != entry or entry in expected:
                    raise HostingError('The backup workbook list is invalid.', False)
                expected.add(entry)
                path = output / entry
                workbook = archive.read(entry)
                verify_workbook(workbook)
                workbooks.append((path, workbook))
                profile['path'] = str(path)
            if set(archive.namelist()) != expected or len(entries) != len(expected):
                raise HostingError('The backup has unexpected files.', False)
            if workbooks:
                output.mkdir(parents=True, exist_ok=True)
                for path, workbook in workbooks:
                    temporary = path.with_suffix(path.suffix + '.partial')
                    temporary.write_bytes(workbook)
                    temporary.replace(path)
    except (BadZipFile, KeyError, TypeError, ValueError, OSError):
        raise HostingError('The backup could not be read or saved on this PC.', False) from None

    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        if has_local_data(store):
            raise HostingError('This PC already has app data. Its files were kept.', False)
        for key, value in manifest['settings'].items():
            if value is not None:
                store.db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)', (key, json.dumps(value)))
        for table, rows in manifest['tables'].items():
            columns = [row['name'] for row in store.db.execute(f'PRAGMA table_info({table})')]
            for row in rows:
                if table in ('jobs', 'portal_jobs') and row['state'] in ('pending', 'retry', 'sending', 'publishing', 'manual_ready'):
                    row['state'] = 'review'
                supported = [column for column in columns if column in row]
                names = ','.join(supported)
                values = ','.join('?' for _ in supported)
                store.db.execute(f'INSERT INTO {table} ({names}) VALUES ({values})', [row[column] for column in supported])


def sync_backup(store, folder, info):
    from .workspace import configuration, connection_for, migration_package, remember_workspace, restore_workspace_configuration

    state = store.get(BACKUP_SETTING, {})
    if not state.get('device'):
        state['device'] = str(uuid4())
        store.set(BACKUP_SETTING, state)
    device = state['device']
    with httpx.Client(timeout=httpx.Timeout(120, connect=10), follow_redirects=False) as http:
        with connection_for(info, http=http) as client:
            metadata = client.call('GET', '/owner/backup')
            remote = metadata['backup']
            direct = bool(metadata.get('direct'))
            restore_pending = state.get('restore_pending', False)
            if remote and remote['device_id'] != device:
                if restore_pending and not has_local_data(store):
                    payload = client.download_backup(remote, direct=direct, workspace_id=info['id'])
                    restore_backup(store, folder, payload, remote['revision'])
                    restored = True
                else:
                    restored = False
                store.set(BACKUP_SETTING, {**state, 'restore_pending': False, 'replica': True,
                          'status': 'Workspace backup restored. Publishing uses the primary PC.' if restored else
                                    'This workspace backup is managed by another PC. Local data was kept.'})
                return restored
            settings_restored = False
            if restore_pending:
                if info.get('migrated') and not has_local_data(store):
                    restore_workspace_configuration(store, client.bootstrap())
                    settings_restored = True
                state = {**state, 'restore_pending': False, 'replica': False, 'status': 'No saved backup yet.'}
                store.set(BACKUP_SETTING, state)
            if not has_local_data(store):
                return settings_restored
            payload = make_backup(store)
            checksum = hashlib.sha256(payload).hexdigest()
            if not remote or checksum != remote['sha256']:
                if len(payload) > metadata.get('max_size', MAX_BACKUP):
                    raise HostingError('The app backup exceeds the private storage size limit.', False)
                remote = client.upload_backup(payload, checksum, device, remote['revision'] if remote else 0,
                                              direct=direct, workspace_id=info['id'])
            store.set(BACKUP_SETTING, {**state, 'restore_pending': False, 'replica': False,
                      'status': 'Workspace backup saved.', 'saved_at': remote['saved_at']})
            if not info.get('migrated'):
                if not info.get('legacy_import') and store.companies():
                    if not state.get('provider_setup_saved'):
                        package = migration_package(store, info['features'], str(uuid4()))
                        client.call('PUT', '/owner/settings', {'profiles': package['profiles']})
                        for provider, values in package['credentials'].items():
                            if provider not in info.get('providers', []):
                                client.call('PUT', '/owner/credentials/' + provider, values)
                        remember_workspace(store, client, client.bootstrap())
                    store.set(BACKUP_SETTING, {**store.get(BACKUP_SETTING, {}),
                              'provider_setup_saved': True,
                              'status': 'Backup saved. Existing buyer access must be verified before importing it.'})
                    return settings_restored
                latest = configuration(store)
                key = latest.get('pending_migration_id') or str(uuid4())
                store.set('owner_workspace', {**latest, 'pending_migration_id': key})
                package = migration_package(store, info['features'], key)
                client.call('POST', '/owner/migrate', package)
                remember_workspace(store, client, client.bootstrap())
            return settings_restored
