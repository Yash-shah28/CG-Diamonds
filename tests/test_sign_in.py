import os
import time

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from drc_sync import sign_in
from drc_sync.app import SIGN_IN_AGAIN, Window, Worker, run_authenticated_desktop
from drc_sync.contract import new_profile
from drc_sync.portal_client import HostingError
from drc_sync.portal_store import PortalStore
from drc_sync.workspace import configuration, token_slot


ORIGIN = 'https://owner.example.test'
BOOTSTRAP = {
    'id': 'company-one', 'name': 'Chintamani Gems', 'features': ['natural', 'lab_grown', 'buyer_feeds'],
    'providers': [], 'migrated': True, 'publishing_enabled': False,
    'feed_url': 'https://buyers.example.test/api/v1/inventory',
    'settings': {'profiles': [{**new_profile(), 'path': 'from-cloud.xlsx'}]},
}


@pytest.fixture
def owner_fixture(monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    vault = {}
    monkeypatch.setenv('CHINTAMANI_WORKSPACE_ORIGIN', ORIGIN)
    monkeypatch.setattr(sign_in.portal_secrets, 'get_secret', vault.get)
    monkeypatch.setattr(sign_in.portal_secrets, 'put_secret', vault.__setitem__)
    monkeypatch.setattr(sign_in.portal_secrets, 'remove_secret', lambda slot: vault.pop(slot, None))
    return app, vault


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def test_first_sign_in_opens_existing_inventory_and_requires_new_password(tmp_path, monkeypatch, owner_fixture):
    app, vault = owner_fixture
    store = PortalStore(tmp_path / 'existing.db')
    profile = {**new_profile(), 'path': str(tmp_path / 'original.xlsx'), 'sheet': 'Stock',
               'mapping': {'Old custom mapping': 'StockId'}}
    store.set('profiles', [profile])
    cid = store.add_company('Existing buyer', 'buyer@example.test', ['Natural'], secret_writer=lambda *_: None)
    store.history(cid, 'Publish', 'Success', 'Existing history', 24)
    history = [tuple(row) for row in store.db.execute('SELECT * FROM portal_history')]
    operations = []

    class Client:
        def __init__(self, origin, token=None):
            self.origin, self.token = origin, token

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def sign_in(self, login, password):
            operations.append('sign in')
            assert (login, password) == ('chintamani-gems', 'temporary-password')
            self.token = 'temporary-session'
            return True

        def change_password(self, temporary, chosen):
            operations.append('change password')
            assert self.token == 'temporary-session'
            assert (temporary, chosen) == ('temporary-password', 'personal-password')
            self.token = 'owner-session'

        def bootstrap(self):
            operations.append('open workspace')
            assert self.token == 'owner-session'
            return BOOTSTRAP

    monkeypatch.setattr(sign_in, 'login_client', Client)
    dialog = sign_in.OwnerSignIn(store)
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    dialog.show()
    dialog.login.setText('chintamani-gems')
    dialog.password.setText('temporary-password')
    dialog.sign_in()
    wait_for(lambda: dialog.pages.currentWidget() is dialog.password_page)
    assert vault == {}
    dialog.new_password.setText('personal-password')
    dialog.confirm_password.setText('different-password')
    dialog.set_password()
    assert dialog.status.text() == 'The passwords do not match.'
    assert operations == ['sign in']
    dialog.confirm_password.setText('personal-password')
    dialog.set_password()
    wait_for(lambda: accepted)
    assert operations == ['sign in', 'change password', 'open workspace']
    assert vault[token_slot(ORIGIN)] == 'owner-session'
    assert configuration(store)['login'] == 'chintamani-gems'
    assert store.get('profiles', []) == [profile]
    assert store.company(cid)['name'] == 'Existing buyer'
    assert [tuple(row) for row in store.db.execute('SELECT * FROM portal_history')] == history
    dialog.deleteLater()
    store.db.close()
    app.processEvents()


def test_saved_sign_in_resumes_or_prompts_when_the_session_expires(tmp_path, monkeypatch, owner_fixture):
    app, vault = owner_fixture
    store = PortalStore(tmp_path / 'owner.db')
    slot = token_slot(ORIGIN)
    vault[slot] = 'old-session'
    calls = []

    class Client:
        def __init__(self, origin, token=None):
            self.origin, self.token = origin, token

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def bootstrap(self):
            calls.append(self.token)
            if self.token == 'old-session':
                error = HostingError('Expired', False)
                error.http_status = 401
                raise error
            return BOOTSTRAP

    monkeypatch.setattr(sign_in, 'login_client', Client)
    unlinked = sign_in.OwnerSignIn(store)
    unlinked.show()
    QTest.qWait(30)
    assert calls == [] and unlinked.isVisible()
    unlinked.reject()
    unlinked.deleteLater()
    store.set('owner_workspace', {'id': BOOTSTRAP['id'], 'origin': ORIGIN})

    expired = sign_in.OwnerSignIn(store)
    expired.show()
    wait_for(lambda: expired.status.text() == 'Your session has ended. Sign in to continue.')
    assert slot not in vault and expired.isVisible()
    expired.reject()
    expired.deleteLater()

    vault[slot] = 'valid-session'
    resumed = sign_in.OwnerSignIn(store)
    accepted = []
    resumed.accepted.connect(lambda: accepted.append(True))
    resumed.show()
    wait_for(lambda: accepted)
    assert calls == ['old-session', 'valid-session']
    resumed.deleteLater()
    store.db.close()
    app.processEvents()


def test_dashboard_waits_for_login_and_sign_out_returns_to_login(tmp_path, monkeypatch, owner_fixture):
    app, vault = owner_fixture
    monkeypatch.setenv('CHINTAMANI_DISABLE_UPDATES', '1')
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    events = []

    class Instance(QObject):
        activated = Signal()

    class Desktop(Window):
        def __init__(self, path):
            events.append('dashboard')
            super().__init__(path)
            QTimer.singleShot(0, self.sign_out_owner)

    class Login(QDialog):
        def __init__(self, store):
            super().__init__()
            self.store = store

        def restore_window(self):
            self.show()

        def exec(self):
            events.append('sign in')
            if events == ['sign in']:
                self.store.set('owner_workspace', {'origin': ORIGIN, 'id': 'company-one',
                                                  'features': BOOTSTRAP['features']})
                return QDialog.Accepted
            return QDialog.Rejected

    monkeypatch.setattr('drc_sync.app.OwnerSignIn', Login)
    monkeypatch.setattr('drc_sync.app.Window', Desktop)
    assert SIGN_IN_AGAIN != 0
    run_authenticated_desktop(app, Instance(), tmp_path / 'main.db', startup=False)
    assert events == ['sign in', 'dashboard', 'sign in']
