"""The desktop owner sign-in gate."""
import httpx

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import portal_secrets
from .brand import app_icon
from .portal_client import HostingError, clean_base
from .workspace import WorkspaceClient, configuration, remember_workspace, sign_in_origin, token_slot


SIGN_IN_STYLE = """
QDialog#signIn { background: #ffffff; }
QWidget { font-family: 'Segoe UI'; color: #0e1930; font-size: 14px; }
QFrame#brandPanel {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #183d73, stop:0.53 #142a50, stop:1 #101a30);
}
QLabel { background: transparent; }
QLabel#brandHeading { color: #ffffff; font-size: 32px; font-weight: 700; }
QLabel#brandContext { color: #c9d8f1; font-size: 15px; }
QLabel#formHeading { font-size: 32px; font-weight: 700; }
QLabel#hint, QLabel#status { color: #50617a; font-size: 13px; }
QLabel#status[error="true"] { color: #b42318; }
QLabel#inputLabel { color: #263958; font-size: 13px; font-weight: 600; }
QLineEdit {
    min-height: 24px; padding: 9px 12px; border: 1px solid #cad5e5;
    border-radius: 9px; background: #f9fbff; selection-background-color: #c8ddff;
}
QLineEdit:focus { border: 2px solid #2864cf; padding: 8px 11px; background: #fff; }
QPushButton {
    min-height: 24px; border: 1px solid #245bc7; padding: 9px 14px; border-radius: 9px;
    background: #245bc7; color: white; font-weight: 600;
}
QPushButton:hover { background: #1849aa; border-color: #1849aa; }
QPushButton:focus { border: 2px solid #0d326e; padding: 8px 13px; }
QPushButton:disabled { background: #e8edf6; border-color: #e8edf6; color: #62748f; }
QPushButton#secondary { background: white; border-color: #cad5e5; color: #284366; }
QPushButton#secondary:hover { background: #eff5ff; }
QPushButton#secondary:disabled { color: #8390a2; }
"""


def caption(text, name, *, wrap=False):
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    label.setTextFormat(Qt.PlainText)
    return label


def input_field(layout, label_text, placeholder='', *, secret=False):
    label = caption(label_text, 'inputLabel')
    field = QLineEdit()
    field.setAccessibleName(label_text)
    field.setPlaceholderText(placeholder)
    label.setBuddy(field)
    if secret:
        field.setEchoMode(QLineEdit.Password)
        field.setMaxLength(128)
    layout.addWidget(label)
    layout.addWidget(field)
    return field


def login_client(origin, token=None):
    return WorkspaceClient(origin, token, timeout=httpx.Timeout(15, connect=5))


class SignInRequest(QThread):
    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.operation()
        except Exception as error:
            self.error = error
        finally:
            self.operation = None


class OwnerSignIn(QDialog):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.request = None
        self.callbacks = None
        self.pending = None
        self.setObjectName('signIn')
        self.setWindowIcon(app_icon())
        self.setWindowTitle('Sign in · Chintamani Gems')
        self.setFixedSize(816, 496)
        self.setStyleSheet(SIGN_IN_STYLE)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        brand = QFrame()
        brand.setObjectName('brandPanel')
        brand.setFixedWidth(296)
        branding = QVBoxLayout(brand)
        branding.setContentsMargins(38, 38, 34, 38)
        branding.setSpacing(15)
        branding.addStretch()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(68, 68))
        branding.addWidget(logo)
        branding.addWidget(caption('Chintamani\nGems', 'brandHeading'))
        branding.addWidget(caption('Diamond inventory', 'brandContext'))
        branding.addStretch()
        outer.addWidget(brand)

        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(52, 32, 52, 24)
        layout.setSpacing(12)
        layout.addStretch()
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)
        self.status = caption('', 'status', wrap=True)
        self.status.setMinimumHeight(34)
        self.status.setAccessibleName('Sign-in status')
        layout.addWidget(self.status)
        layout.addStretch()
        outer.addWidget(form)

        self.login_page = QWidget()
        login_layout = QVBoxLayout(self.login_page)
        login_layout.setContentsMargins(0, 0, 0, 0)
        login_layout.setSpacing(8)
        login_layout.addWidget(caption('Sign in', 'formHeading'))
        login_layout.addWidget(caption('Open your company workspace.', 'hint'))
        login_layout.addSpacing(13)
        self.login = input_field(login_layout, 'Login ID', 'e.g. chintamani-gems')
        self.login.setText(configuration(store).get('login', ''))
        self.login.setMaxLength(32)
        login_layout.addSpacing(6)
        self.password = input_field(login_layout, 'Password', secret=True)
        login_layout.addSpacing(12)
        self.submit = QPushButton('Sign in')
        self.submit.clicked.connect(self.sign_in)
        self.submit.setDefault(True)
        login_layout.addWidget(self.submit)
        self.setup = QPushButton('Set workspace server')
        self.setup.setObjectName('secondary')
        self.setup.setAutoDefault(False)
        self.setup.clicked.connect(self.set_server)
        login_layout.addWidget(self.setup, alignment=Qt.AlignLeft)
        self.pages.addWidget(self.login_page)

        self.password_page = QWidget()
        password_layout = QVBoxLayout(self.password_page)
        password_layout.setContentsMargins(0, 0, 0, 0)
        password_layout.setSpacing(8)
        password_layout.addWidget(caption('Set your password', 'formHeading'))
        password_layout.addWidget(caption('Choose at least 12 characters to finish signing in.', 'hint'))
        password_layout.addSpacing(13)
        self.new_password = input_field(password_layout, 'New password', secret=True)
        password_layout.addSpacing(6)
        self.confirm_password = input_field(password_layout, 'Confirm password', secret=True)
        password_layout.addSpacing(12)
        self.save_password = QPushButton('Set password and continue')
        self.save_password.clicked.connect(self.set_password)
        password_layout.addWidget(self.save_password)
        self.back = QPushButton('Back to sign in')
        self.back.setObjectName('secondary')
        self.back.setAutoDefault(False)
        self.back.clicked.connect(self.reset_login)
        password_layout.addWidget(self.back, alignment=Qt.AlignLeft)
        self.pages.addWidget(self.password_page)

        try:
            self.origin = sign_in_origin(store)
        except (ValueError, OSError):
            self.origin = ''
        self.setup.setVisible(not self.origin and not configuration(store).get('id'))
        if not self.origin:
            self.message('The workspace server needs to be configured.', error=True)
        focus = self.password if self.login.text() else self.login
        focus.setFocus()
        QTimer.singleShot(0, self.resume)

    def message(self, text='', *, error=False):
        self.status.setProperty('error', error)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)

    def set_server(self):
        value, ok = QInputDialog.getText(self, 'Workspace server', 'Workspace server address')
        if not ok:
            return
        try:
            self.origin = clean_base(value)
        except ValueError as error:
            self.message(str(error), error=True)
            return
        self.store.set('workspace_setup_origin', self.origin)
        self.setup.hide()
        self.message()
        self.login.setFocus()

    def resume(self):
        if not self.origin or not configuration(self.store).get('id') or self.request:
            return
        try:
            token = portal_secrets.get_secret(token_slot(self.origin))
        except Exception:
            self.message('Could not read the saved sign-in from Windows Credential Manager.', error=True)
            return
        if not token:
            return

        def check():
            with login_client(self.origin, token) as client:
                return client, client.bootstrap()

        self.run_request(check, lambda result: self.finish(*result), 'Checking your saved sign-in…',
                         on_failure=self.resume_failed)

    def resume_failed(self, error):
        if getattr(error, 'http_status', None) in (401, 403):
            portal_secrets.remove_secret(token_slot(self.origin))
            self.message('Your session has ended. Sign in to continue.')
        else:
            self.show_error(error)

    def sign_in(self):
        if self.request or self.pages.currentWidget() is not self.login_page:
            return
        if not self.origin:
            self.set_server()
            return
        login, password = self.login.text().strip(), self.password.text()
        if not login or not password:
            self.message('Enter your Login ID and password.', error=True)
            (self.password if login else self.login).setFocus()
            return

        def authenticate():
            with login_client(self.origin) as client:
                change_required = client.sign_in(login, password)
                return client, None if change_required else client.bootstrap()

        def signed_in(result):
            client, info = result
            self.password.clear()
            if info is None:
                self.pending = (client.token, login, password)
                self.pages.setCurrentWidget(self.password_page)
                self.save_password.setDefault(True)
                self.new_password.setFocus()
            else:
                self.finish(client, info, login=login)

        self.run_request(authenticate, signed_in, 'Signing in…')

    def set_password(self):
        if self.request or not self.pending:
            return
        chosen = self.new_password.text()
        if len(chosen) < 12:
            self.message('Use at least 12 characters.', error=True)
            self.new_password.setFocus()
            return
        if chosen != self.confirm_password.text():
            self.message('The passwords do not match.', error=True)
            self.confirm_password.setFocus()
            return
        token, login, temporary = self.pending
        if chosen == temporary:
            self.message('Choose a password different from the temporary password.', error=True)
            return

        def change():
            with login_client(self.origin, token) as client:
                client.change_password(temporary, chosen)
                return client

        def changed(client):
            self.pending = None
            self.new_password.clear()
            self.confirm_password.clear()
            self.run_request(lambda: self.check_changed_password(client.token),
                             lambda result: self.finish(*result, login=login), 'Opening your workspace…',
                             on_failure=self.password_saved)

        self.run_request(change, changed, 'Saving your password…')

    def check_changed_password(self, token):
        with login_client(self.origin, token) as client:
            return client, client.bootstrap()

    def password_saved(self, error):
        self.reset_login()
        self.message('Your password was saved. Sign in with your new password to continue.', error=True)

    def run_request(self, operation, on_success, status, *, on_failure=None):
        self.pages.setEnabled(False)
        self.message(status)
        self.callbacks = on_success, on_failure
        self.request = SignInRequest(operation, self)
        self.request.finished.connect(self.request_finished)
        self.request.start()

    def request_finished(self):
        request, (on_success, on_failure) = self.request, self.callbacks
        self.request = None
        self.callbacks = None
        self.pages.setEnabled(True)
        self.message()
        try:
            if request.error:
                (on_failure or self.show_error)(request.error)
            else:
                on_success(request.result)
        except Exception as error:
            self.show_error(error)
        finally:
            request.deleteLater()

    def show_error(self, error):
        text = str(error) if isinstance(error, HostingError) else 'Could not sign in. Please try again.'
        self.message(text, error=True)

    def finish(self, client, info, *, login=None):
        remember_workspace(self.store, client, info, login=login)
        self.accept()

    def reset_login(self):
        self.pending = None
        self.new_password.clear()
        self.confirm_password.clear()
        self.pages.setCurrentWidget(self.login_page)
        self.submit.setDefault(True)
        self.password.setFocus()
        self.message()

    def reject(self):
        if self.request:
            return
        self.pending = None
        self.password.clear()
        self.new_password.clear()
        self.confirm_password.clear()
        super().reject()

    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
