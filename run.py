import sys
import os
import traceback
from pathlib import Path

def _excepthook(exc_type, exc_val, exc_tb):
    try:
        tb = ''.join(traceback.format_exception(exc_type, exc_val, exc_tb))
        root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'DRCInventorySync'
        root.mkdir(parents=True, exist_ok=True)
        (root / 'crash.log').write_text(tb, encoding='utf-8')
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_val, exc_tb)

sys.excepthook = _excepthook

from drc_sync.app import main
main()
