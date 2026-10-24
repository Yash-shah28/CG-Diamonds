"""One inventory worker per data folder, with activation on a second launch."""
import hashlib
from PySide6.QtCore import QObject, QLockFile, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class Instance(QObject):
    activated = Signal()

    def __init__(self, root):
        super().__init__()
        identity = str(root.resolve()).casefold().encode()
        self.name = 'DRCInventorySync-' + hashlib.sha256(identity).hexdigest()[:24]
        self.lock = QLockFile(str(root / 'app.lock'))
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self._activate)

    def acquire(self):
        if not self.lock.tryLock(100):
            return False
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            self.lock.unlock()
            raise RuntimeError('Cannot create the local application activation channel')
        return True

    def notify(self):
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        connected = socket.waitForConnected(1500)
        socket.abort()
        return connected

    def _activate(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.close()
            socket.deleteLater()
            self.activated.emit()

    def close(self):
        self.server.close()
        self.lock.unlock()
