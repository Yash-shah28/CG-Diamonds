"""Connect an existing Windows installation to its owner workspace."""
from . import portal_secrets
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton

from .dialogs import dialog_layout
from .backup import BACKUP_SETTING, publication_blocked
from .portal_client import HostingError
from .workspace import CLOUD, SETTING, configuration, connection_for, migration_package, remember_workspace, token_slot


FIELDS = {
    'rapaport': ('client_id', 'client_secret'),
    'hrd': ('token',),
    'email': ('sender', 'password'),
}


def _message(error):
    return str(error) if isinstance(error, HostingError) else 'The workspace operation could not be completed.'


def owner_workspace_dialog(window):
    dialog = QDialog(window)
    dialog.setWindowTitle('Owner workspace')
    dialog.resize(560, 340)
    layout = dialog_layout(dialog)
    info = configuration(window.store)
    company = QLabel(info.get('name', 'Owner workspace'))
    company.setTextFormat(Qt.PlainText)
    company.setObjectName('dialogTitle')
    layout.addWidget(company)
    status = QLabel()
    status.setTextFormat(Qt.PlainText)
    status.setWordWrap(True)
    layout.addWidget(status)
    refresh = QPushButton('Refresh company access')
    backup = QPushButton('Back up now')
    backup_status = QLabel()
    backup_status.setWordWrap(True)
    backup_status.setTextFormat(Qt.PlainText)
    layout.addWidget(backup_status)
    migrate = QPushButton('Import settings from this PC')
    activate = QPushButton('Use workspace publishing')
    credentials = QPushButton('Manage integration credentials')
    change_password = QPushButton('Change password')
    sign_out = QPushButton('Sign out')
    for item in (refresh, backup, migrate, activate, credentials, change_password, sign_out):
        layout.addWidget(item)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    def refresh_state():
        current = configuration(window.store)
        connected = bool(current.get('id') and portal_secrets.get_secret(token_slot(current['origin'])))
        change_password.setVisible(connected)
        sign_out.setVisible(connected)
        refresh.setVisible(connected)
        backup.setVisible(connected and current.get('backup_enabled', False))
        backup_status.setVisible(backup.isVisibleTo(dialog))
        backup_status.setText(window.store.get(BACKUP_SETTING, {}).get('status', 'Automatic backup is on.'))
        migrate.setVisible(connected and not current.get('migrated') and not current.get('backup_enabled'))
        activate.setVisible(connected and current.get('publishing_enabled') and not current.get(CLOUD)
                            and not publication_blocked(window.store))
        credentials.setVisible(connected)
        if connected:
            state = 'Workspace publishing active' if current.get(CLOUD) else 'Existing publisher active'
            status.setText(state)
        window.apply_workspace_features()

    def sync_status():
        try:
            with connection_for(configuration(window.store)) as client:
                remember_workspace(window.store, client, client.bootstrap())
            refresh_state()
        except Exception as error:
            status.setText(_message(error))

    def import_settings():
        current = configuration(window.store)
        identifier = current.get('pending_migration_id') or str(uuid4())
        try:
            package = migration_package(window.store, current['features'], identifier)
            names = ', '.join(package['credentials']) or 'None'
            preview = (f"Import {len(package['profiles'])} Excel connection(s) and {len(package['buyers'])} buyer feed(s)?\n\n"
                       f"Integration credentials found: {names}. Buyer access continues on the existing endpoint.")
            if QMessageBox.question(dialog, 'Import local settings', preview) != QMessageBox.Yes:
                return
            window.store.set(SETTING, {**current, 'pending_migration_id': identifier})
            with connection_for(current) as client:
                client.call('POST', '/owner/migrate', package)
                remember_workspace(window.store, client, client.bootstrap())
            refresh_state()
            status.setText('Settings imported. The platform admin can enable workspace publishing after verifying the live feed.')
        except Exception as error:
            status.setText(_message(error))

    def use_workspace():
        try:
            current = configuration(window.store)
            if publication_blocked(window.store):
                raise HostingError('Publishing stays on the primary PC until the workspace is handed off.', False)
            with connection_for(current) as client:
                current = remember_workspace(window.store, client, client.bootstrap())
            if not current.get('publishing_enabled'):
                raise HostingError('The platform admin must enable publishing first.', False)
            if window.rapaport_page.busy:
                raise HostingError('Wait for the current Rapaport operation to finish.', False)
            if not window.worker.publisher_lock.acquire(blocking=False):
                raise HostingError('Wait for the current inventory operation to finish.', False)
            try:
                window.store.set(SETTING, {**current, CLOUD: True})
            finally:
                window.worker.publisher_lock.release()
            window.rapaport_page.refresh_source()
            refresh_state()
        except Exception as error:
            status.setText(_message(error))

    def manage_credentials():
        current = configuration(window.store)
        enabled = sorted(set(current['features']) & set(FIELDS))
        if not enabled:
            status.setText('This workspace has no enabled integrations.')
            return
        provider, ok = QInputDialog.getItem(dialog, 'Integration credentials', 'Provider', enabled, editable=False)
        if not ok:
            return
        values = {}
        for field in FIELDS[provider]:
            mode = QLineEdit.Normal if field in ('sender', 'client_id') else QLineEdit.Password
            value, ok = QInputDialog.getText(dialog, 'Integration credentials', field.replace('_', ' ').title(), mode)
            if not ok:
                return
            values[field] = value
        try:
            with connection_for(current) as client:
                client.call('PUT', '/owner/credentials/' + provider, values)
                remember_workspace(window.store, client, client.bootstrap())
            window.rapaport_page.refresh_source()
            refresh_state()
            status.setText('Integration credentials saved securely in your workspace.')
        except Exception as error:
            status.setText(_message(error))

    def change_current_password():
        current_info = configuration(window.store)
        previous, ok = QInputDialog.getText(dialog, 'Change password', 'Current password', QLineEdit.Password)
        if not ok:
            return
        chosen, ok = QInputDialog.getText(dialog, 'Change password', 'New password', QLineEdit.Password)
        if not ok:
            return
        try:
            with connection_for(current_info) as client:
                client.change_password(previous, chosen)
                remember_workspace(window.store, client, client.bootstrap())
            status.setText('Password changed.')
        except Exception as error:
            status.setText(_message(error))

    def end_session():
        try:
            if window.sign_out_owner():
                dialog.accept()
            else:
                status.setText('Wait for the current inventory operation to finish before signing out.')
        except Exception as error:
            status.setText(_message(error))

    change_password.clicked.connect(change_current_password)
    sign_out.clicked.connect(end_session)
    refresh.clicked.connect(sync_status)
    backup.clicked.connect(window.start_workspace_backup)
    window.backup_worker.finished.connect(refresh_state)
    dialog.finished.connect(lambda: window.backup_worker.finished.disconnect(refresh_state))
    migrate.clicked.connect(import_settings)
    activate.clicked.connect(use_workspace)
    credentials.clicked.connect(manage_credentials)
    refresh_state()
    dialog.exec()
