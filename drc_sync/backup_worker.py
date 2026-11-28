"""Run workspace backups separately from the inventory publisher."""
from PySide6.QtCore import QThread, Signal

from .backup import BACKUP_SETTING, sync_backup
from .portal_client import HostingError
from .portal_store import PortalStore
from .workspace import workspace_context


class BackupWorker(QThread):
    restored = Signal()

    def __init__(self, dbpath, parent=None):
        super().__init__(parent)
        self.dbpath = dbpath

    def run(self):
        store = PortalStore(self.dbpath)
        try:
            info = workspace_context(store)
            if info and info.get('backup_enabled') and sync_backup(store, self.dbpath.parent, info):
                self.restored.emit()
        except Exception as error:
            message = str(error) if isinstance(error, HostingError) else 'Workspace backup unavailable. It will retry.'
            state = store.get(BACKUP_SETTING, {})
            store.set(BACKUP_SETTING, {**state, 'status': message})
        finally:
            store.db.close()
