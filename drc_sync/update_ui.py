"""Qt update controller; inventory processing is drained before restart."""
import os
from pathlib import Path
from PySide6.QtCore import QThread, Signal, QTimer, QObject, Qt
from PySide6.QtWidgets import QMessageBox
from .updater import latest, download, update_client, launch_installer
from .version import VERSION


class UpdateTask(QThread):
    result = Signal(object, object)
    failed = Signal(str)

    def __init__(self, folder, parent=None, download_installer=True):
        super().__init__(parent); self.folder = folder
        self.download_installer = download_installer

    def run(self):
        try:
            with update_client() as client:
                release = latest(client)
                path = download(client, release, self.folder) if release and self.download_installer else None
            self.result.emit(release, path)
        except Exception as exc:
            # Exception text can include URLs or proxy credentials; never display it.
            self.failed.emit(type(exc).__name__)


class UpdateController(QObject):
    def __init__(self, window, folder, can_install=True):
        super().__init__(window)
        self.window = window; self.folder = Path(folder); self.task = None
        self.ready = None; self.manual = False; self.installing = False
        self.can_install = can_install
        self.timer = QTimer(self); self.timer.setInterval(6 * 60 * 60 * 1000)
        self.timer.timeout.connect(self.check); self.timer.start()
        QTimer.singleShot(15000, self, self.check)
        window.update_button.clicked.connect(self.clicked)
        window.worker.finished.connect(self.worker_stopped)

    def clicked(self):
        if self.ready:
            self.offer_install()
        else:
            self.check(True)

    def check(self, manual=False):
        if self.installing:
            return
        if self.task and self.task.isRunning():
            if manual:
                self.manual = True
                self.window.update_button.setText("Checking for updates...")
                self.window.update_button.setToolTip("An update check is already running. Its result will be shown when it finishes.")
            return
        if self.ready:
            return
        self.manual = manual
        self.window.update_button.setText('Checking for updates…')
        self.window.update_button.setToolTip("Checking the signed release feed and downloading any newer installer.")
        self.task = UpdateTask(self.folder, self, download_installer=self.can_install)
        self.task.result.connect(self.received); self.task.failed.connect(self.failed)
        self.task.start()

    def received(self, release, path):
        self.window.update_button.setToolTip("Chintamani Gems "+VERSION)
        if not release:
            self.window.update_button.setText('Check for updates')
            if self.manual:
                QMessageBox.information(self.window, 'Application updates', f'You are using the latest published version ({VERSION}).')
            return
        if not self.can_install:
            self.window.update_button.setText("Check for updates")
            if self.manual:
                QMessageBox.information(self.window, "Update available",
                    "Version "+release["version"]+" is available. You are running the Python source version. "
                    "Download the Windows installer from https://chintamani-updates.vercel.app/api/download to install it.")
            return
        self.ready = (release, path)
        self.window.update_button.setText('Install update '+release['version'])
        self.window.tray.showMessage('Update ready', 'Chintamani Gems '+release['version']+' is ready. Open the app and select Install update.')
        if self.manual:
            self.offer_install()

    def failed(self, failure):
        self.window.update_button.setText('Check for updates')
        self.window.update_button.setToolTip('Update check unavailable; automatically checks again later.')
        if self.manual:
            QMessageBox.warning(self.window, 'Updates unavailable', 'The update could not be verified or downloaded. Your current version can still be used. Try again later. ('+failure+')')

    def offer_install(self):
        if not self.ready or self.installing:
            return
        release, _ = self.ready
        message = 'Install Chintamani Gems '+release['version']+' and restart?\n\nAny upload already in progress will finish first. Your Excel connections, rates and history will be kept.'
        if release.get('notes'):
            message += '\n\n'+release['notes']
        box = QMessageBox(self.window); box.setWindowTitle('Update ready'); box.setTextFormat(Qt.PlainText); box.setText(message)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel); box.button(QMessageBox.Yes).setText('Install and restart')
        if box.exec() != QMessageBox.Yes:
            return
        self.installing = True
        self.window.update_button.setText('Waiting for uploads to finish…'); self.window.update_button.setEnabled(False)
        self.window.centralWidget().setEnabled(False)
        self.window.worker.requestInterruption()
        if not self.window.worker.isRunning():
            self.worker_stopped()

    def worker_stopped(self):
        if not self.installing:
            return
        try:
            release, path = self.ready
            launch_installer(path, release)
        except Exception:
            QMessageBox.warning(self.window, 'Update could not start', 'Please reopen the app to resume monitoring and try the update again.')
            self.window.exit()
            return
        self.window.exit()

    def stop(self):
        self.timer.stop()
        if self.task and self.task.isRunning():
            self.task.wait()  # bounded HTTP timeouts; never destroy a live QThread
