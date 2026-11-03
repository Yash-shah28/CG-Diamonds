"""Safe, actionable workbook notifications; never reflect arbitrary library messages."""
from pathlib import Path
from zipfile import BadZipFile
from .core import WorkbookValidationError
from .responses import redact_text

def workbook_error(profile, exc):
    context = f"{profile.get('kind', 'Inventory')} - {Path(profile.get('path', '')).name}"
    if profile.get('sheet'):context += ' ['+str(profile['sheet'])+']'
    if isinstance(exc, WorkbookValidationError):
        detail = str(exc)
    elif isinstance(exc, FileNotFoundError):
        detail = 'Workbook not found. Check that the file or network drive is available, or reconnect it in Excel Mapping.'
    elif getattr(exc, 'winerror', None) in (32, 33):
        detail = 'Workbook is locked by another program. Close that program, then save the workbook again.'
    elif isinstance(exc, PermissionError):
        detail = 'Workbook access denied. Check file/folder read permissions and whether another program has locked the file.'
    elif isinstance(exc, BadZipFile):
        detail = 'Workbook is not a readable .xlsx file. It may be damaged or password-encrypted. Save an unencrypted .xlsx copy in Excel.'
    elif isinstance(exc, OSError):
        detail = f'File could not be read (system error {getattr(exc, "winerror", None) or exc.errno or "unknown"}). Check the drive, file access and available storage.'
    else:
        detail = f'Workbook processing failed ({type(exc).__name__}). Reopen and save the .xlsx file, then check Excel Mapping.'
    return redact_text(context+'\n'+detail)[:1800]
