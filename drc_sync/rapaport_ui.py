"""Rapaport Trade credentials and manual Natural inventory uploads."""
import hashlib
import json
from pathlib import Path

import httpx
from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from . import portal_secrets
from .rapaport import RapaportClient, RapaportError, prepare_natural_upload
from .responses import redact_text
from .ui import card, label
from .workbook_errors import workbook_error


CREDENTIAL_SLOT = 'rapaport-api'
UPLOAD_SETTING = 'rapaport.last_upload'


class _Task(QThread):
    def __init__(self, parent, action, operation):
        super().__init__(parent)
        self.action = action
        self.operation = operation
        self.result = None
        self.error = ''

    def run(self):
        try:
            self.result = self.operation()
        except RapaportError as error:
            self.error = str(error)
        except Exception:
            self.error = 'The Rapaport operation could not be completed.'
        finally:
            self.operation = None


class RapaportPage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.store = window.store
        self.client = RapaportClient()
        self._task = None
        self.credentials = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        credentials_card, credentials_layout = card()
        credentials_layout.addWidget(label('API credentials', 'cardTitle'))
        form = QFormLayout()
        self.client_id = QLineEdit()
        self.client_secret = QLineEdit()
        self.client_secret.setEchoMode(QLineEdit.Password)
        form.addRow('Client ID', self.client_id)
        form.addRow('Client Secret', self.client_secret)
        credentials_layout.addLayout(form)
        credential_actions = QHBoxLayout()
        self.save_button = QPushButton('Save credentials')
        self.test_button = QPushButton('Test connection')
        self.test_button.setObjectName('quiet')
        credential_actions.addWidget(self.save_button)
        credential_actions.addWidget(self.test_button)
        credential_actions.addStretch()
        credentials_layout.addLayout(credential_actions)
        layout.addWidget(credentials_card)

        inventory_card, inventory_layout = card()
        inventory_layout.addWidget(label('Natural inventory', 'cardTitle'))
        self.source = label('', 'fileInfo')
        self.source.setTextFormat(Qt.PlainText)
        inventory_layout.addWidget(self.source)
        inventory_layout.addWidget(label('Add & Update keeps listings that are missing from this workbook.', 'muted'))
        upload_actions = QHBoxLayout()
        self.upload_button = QPushButton('Upload Natural diamonds')
        self.status_button = QPushButton('Check upload status')
        self.status_button.setObjectName('quiet')
        upload_actions.addWidget(self.upload_button)
        upload_actions.addWidget(self.status_button)
        upload_actions.addStretch()
        inventory_layout.addLayout(upload_actions)
        self.latest_status = label('', 'muted')
        self.latest_status.setTextFormat(Qt.PlainText)
        self.latest_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        inventory_layout.addWidget(self.latest_status)
        layout.addWidget(inventory_card)
        self.notice = label('', 'muted')
        self.notice.setTextFormat(Qt.PlainText)
        layout.addWidget(self.notice)
        layout.addStretch()

        self.save_button.clicked.connect(self.save_credentials)
        self.test_button.clicked.connect(self.test_connection)
        self.upload_button.clicked.connect(self.prepare_upload)
        self.status_button.clicked.connect(self.check_status)
        try:
            stored = portal_secrets.get_secret(CREDENTIAL_SLOT)
            if stored:
                self.credentials = json.loads(stored)
                self.client_id.setText(self.credentials['client_id'])
                self.client_secret.setPlaceholderText('Client Secret saved')
        except Exception:
            self.credentials = {}
            self.notice.setText('Could not read Rapaport credentials from Windows Credential Manager.')
        self.client_id.textChanged.connect(self.update_buttons)
        self.client_secret.textChanged.connect(self.update_buttons)
        self.refresh_source()

    @property
    def busy(self):
        return self._task is not None

    def natural_profile(self):
        return next((profile for profile in self.store.get('profiles', [])
                     if profile.get('kind') == 'Natural' and profile.get('path')), None)

    def account_ref(self):
        identifier = self.credentials.get('client_id', '')
        return hashlib.sha256(identifier.encode()).hexdigest()

    def last_upload(self):
        upload = self.store.get(UPLOAD_SETTING, {})
        return upload if upload.get('account') == self.account_ref() else {}

    def refresh_source(self):
        profile = self.natural_profile()
        description = 'Connect the Natural workbook in Excel Mapping.'
        if profile:
            description = f"{Path(profile['path']).name}  •  {profile['sheet']}"
        self.source.setText(description)
        self.update_buttons()

    def update_buttons(self):
        upload = self.last_upload()
        enabled = not self.busy
        for widget in (self.client_id, self.client_secret, self.save_button):
            widget.setEnabled(enabled)
        saved = bool(self.credentials) and self.client_id.text().strip() == self.credentials['client_id'] and not self.client_secret.text()
        self.test_button.setEnabled(enabled and saved)
        self.upload_button.setEnabled(enabled and saved and bool(self.natural_profile())
                                      and (not upload or upload.get('finished', False)))
        self.status_button.setEnabled(enabled and saved and bool(upload))
        if upload:
            results = ', '.join(f"{upload[key]} {caption}" for key, caption in (
                ('received', 'received'), ('valid', 'valid'), ('invalid', 'invalid')) if key in upload)
            suffix = f'\n{results}' if results else ''
            if upload.get('messages'):
                suffix += '\n' + upload['messages']
            self.latest_status.setText(f"{upload['status']}\nUpload ID {upload['id']}{suffix}")
        else:
            self.latest_status.setText('No upload recorded for this account.')

    def save_credentials(self):
        client_id = self.client_id.text().strip()
        secret = self.client_secret.text().strip()
        if not secret and client_id == self.credentials.get('client_id'):
            secret = self.credentials.get('client_secret', '')
        if not client_id or not secret:
            self.notice.setText('Enter both the Rapaport Client ID and Client Secret.')
            return
        credentials = {'client_id': client_id, 'client_secret': secret}
        try:
            portal_secrets.put_secret(CREDENTIAL_SLOT, json.dumps(credentials))
        except Exception:
            self.notice.setText('Could not save Rapaport credentials in Windows Credential Manager.')
            return
        self.credentials = credentials
        self.client.clear_token()
        self.client_secret.clear()
        self.client_secret.setPlaceholderText('Client Secret saved')
        self.notice.setText('Rapaport credentials saved.')
        self.update_buttons()

    def _start(self, action, message, operation):
        if self.busy:
            return
        self.notice.setText(message)
        self._task = _Task(self, action, operation)
        self._task.finished.connect(self.finish)
        self.update_buttons()
        self._task.start()

    def _start_network(self, action, message, operation):
        credentials = self.credentials.copy()

        def request():
            with httpx.Client(timeout=httpx.Timeout(45, connect=10), follow_redirects=False) as http:
                return operation(http, credentials)

        self._start(action, message, request)

    def test_connection(self):
        def test(http, credentials):
            self.client.clear_token()
            self.client.login(http, credentials)

        self._start_network('test', 'Checking Rapaport credentials…', test)

    def prepare_upload(self):
        profile = self.natural_profile()
        if not profile:
            self.refresh_source()
            return

        def prepare():
            try:
                return prepare_natural_upload(profile)
            except Exception as error:
                raise RapaportError(workbook_error(profile, error)) from None

        self._start('prepare', 'Validating the Natural workbook…', prepare)

    def _confirm(self, prepared):
        message = (
            f"Upload {prepared.records:,} Natural diamonds from {prepared.path.name} to Rapaport Trade?\n\n"
            "Add & Update will add new listings and update matching stock numbers using the workbook prices."
        )
        box = QMessageBox(self)
        box.setWindowTitle('Upload Natural diamonds')
        box.setTextFormat(Qt.PlainText)
        box.setText(message)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        box.button(QMessageBox.Yes).setText('Upload to Rapaport')
        if box.exec() == QMessageBox.Yes:
            self._start_network('upload', 'Submitting the Natural inventory…',
                                lambda http, credentials: self.client.upload(http, credentials, prepared))
        else:
            self.notice.setText('Upload canceled.')

    def check_status(self):
        uid = self.last_upload().get('id')
        if uid:
            self._start_network('status', 'Checking Rapaport upload status…',
                                lambda http, credentials: self.client.status(http, credentials, uid))

    @Slot()
    def finish(self):
        task = self._task
        if task is None:
            return
        self._task = None
        try:
            if task.error:
                self.notice.setText(task.error)
            elif task.action == 'test':
                self.notice.setText('Rapaport accepted the saved credentials.')
            elif task.action == 'prepare':
                self._confirm(task.result)
            elif task.action == 'upload':
                self.store.set(UPLOAD_SETTING, {
                    'account': self.account_ref(), 'id': task.result,
                    'status': 'Submitted to Rapaport. Check upload status for the result.',
                })
                self.notice.setText('')
            elif task.action == 'status':
                self._save_status(task.result)
                self.notice.setText('')
        except Exception:
            if task.action == 'upload':
                self.notice.setText(f'Upload submitted. Could not save upload ID {task.result} locally. Check Rapaport Trade Upload History.')
            else:
                self.notice.setText('Could not update the local Rapaport status.')
        finally:
            task.deleteLater()
            self.update_buttons()

    def _save_status(self, body):
        secrets = tuple(self.credentials.values())
        state = body['status'].strip()
        finished = bool(body.get('endTime')) or state.lower().startswith(
            ('finished', 'completed', 'failed', 'canceled', 'cancelled'))
        result = {**self.last_upload(), 'status': redact_text(state, secrets)[:200], 'finished': finished}
        for target, key in (('received', 'numLotReceived'), ('valid', 'numValidLots'), ('invalid', 'numInvalidLots')):
            count = body.get(key)
            if type(count) is int and count >= 0:
                result[target] = count
        messages = []
        for key in ('errorMessages', 'warningMessages'):
            value = body.get(key)
            if isinstance(value, str):
                messages.append(value)
            elif isinstance(value, list):
                messages.extend(item for item in value if isinstance(item, str))
        result['messages'] = redact_text('\n'.join(messages[:8]), secrets)[:1200]
        self.store.set(UPLOAD_SETTING, result)

