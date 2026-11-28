from pathlib import Path
import hashlib
from uuid import uuid4

from openpyxl import Workbook, load_workbook
import pytest

from drc_sync.backup import make_backup, restore_backup
from drc_sync.contract import new_profile
from drc_sync.portal_client import HostingError
from drc_sync.portal_store import PortalStore


def test_restore_rebuilds_the_rates_table_and_shows_existing_rapaport_status(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from drc_sync import portal_secrets
    from drc_sync.app import Window, Worker

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(portal_secrets, 'get_secret', lambda _: None)
    monkeypatch.setattr(Worker, 'start', lambda _: None)
    source = PortalStore(tmp_path / 'source.db')
    buyer = source.add_company('Buyer', 'buyer@example.test', ['Natural'], secret_writer=lambda *_: None)
    reference = hashlib.sha256(b'rapaport-account').hexdigest()
    source.set('rapaport.last_upload', {'id': 'example-upload', 'account': reference, 'status': 'Complete'})
    payload = make_backup(source)
    source.db.close()

    window = Window(tmp_path / 'target.db')
    try:
        assert window.rate_table.rowCount() == 0
        window.store.set('owner_workspace', {
            'id': 'workspace', 'features': ['natural', 'rapaport', 'buyer_feeds'],
            'providers': ['rapaport'], 'provider_refs': {'rapaport': reference},
        })
        restore_backup(window.store, tmp_path, payload, 1)
        window.reload_workspace_backup()
        assert window.clients_table.rowCount() == window.rate_table.rowCount() == 1
        assert window.store.company(buyer)['name'] == 'Buyer'
        assert window.rapaport_page.last_upload()['id'] == 'example-upload'
    finally:
        window.backup_timer.stop()
        window.timer.stop()
        window.tray.hide()
        window.store.db.close()
        window.deleteLater()
        app.processEvents()


def test_backup_restores_inventory_workbook_and_buyer_history_on_empty_pc(tmp_path):
    source = PortalStore(tmp_path / 'old.db')
    target = PortalStore(tmp_path / 'new.db')
    workbook_path = tmp_path / 'Stock.xlsx'
    book = Workbook()
    book.active.append(['Stock No', 'Weight'])
    book.active.append(['S1', 1.2])
    book.save(workbook_path)
    profile = {**new_profile(), 'path': str(workbook_path), 'mapping': {'Stock No': 'StockId'}}
    source.set('profiles', [profile])
    source.set('website_rates', {'client': {'mode': 'increase', 'percent': '5'}})
    source.set('owner_workspace', {'token': 'local-session-never-restore'})
    source.cache(profile, {'diamonds': [{'StockId': 'S1'}]})
    buyer = source.add_company('Sample buyer', 'buyer@example.test', ['Natural'], secret_writer=lambda *_: None)
    source.history(buyer, 'Publish', 'Success', 'Backup fixture', 1)
    source.db.execute("""
        INSERT INTO portal_jobs(client_id,revision,action,payload,state,created,key_hash,kinds)
        VALUES (?,1,'publish','{}','pending',1,?,'["Natural"]')
    """, (buyer, source.company(buyer)['key_hash']))
    source.db.commit()
    try:
        payload = make_backup(source)
        assert make_backup(source) == payload
        restore_backup(target, tmp_path, payload, 1)
        restored_profile = target.get('profiles', [])[0]
        restored_file = Path(restored_profile['path'])
        assert restored_file != workbook_path and restored_file.read_bytes() == workbook_path.read_bytes()
        assert restored_profile['mapping'] == profile['mapping']
        assert target.company(buyer)['key_hash'] == source.company(buyer)['key_hash']
        assert target.get('website_rates', {}) == source.get('website_rates', {})
        assert target.get('owner_workspace', {}) == {}
        assert target.pending() is None
        assert target.db.execute('SELECT state FROM portal_jobs').fetchone()[0] == 'review'
        assert target.db.execute('SELECT COUNT(*) FROM portal_cache').fetchone()[0] == 1
        assert target.db.execute("SELECT records FROM portal_history WHERE event='Publish'").fetchone()[0] == 1
        with pytest.raises(HostingError, match='already has app data'):
            restore_backup(target, tmp_path, payload, 2)
    finally:
        source.db.close()
        target.db.close()
