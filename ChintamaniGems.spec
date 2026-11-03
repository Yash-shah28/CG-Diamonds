# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('drc_sync/assets', 'drc_sync/assets')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('keyring')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['drc_sync._bundled_credentials'],
    noarchive=False,
    optimize=0,
)
# Qt 6.12 uses Windows system ICU; exclude unrelated runtime ICU copies.
a.binaries = [item for item in a.binaries if item[0].replace('\\', '/').split('/')[-1].lower() != 'icuuc.dll']
# Some managed Python runtimes load this policy file inside SSLContext.__new__.
# Preserve the runtime's certificate policy beside frozen ssl.py; never disable TLS verification.
from pathlib import Path
import ssl
ssl_policy = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
if ssl_policy.is_file():
    a.datas.append(('codex-rejected-cas.pem', str(ssl_policy), 'DATA'))
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ChintamaniGems',
    icon='drc_sync/assets/chintamani-gems.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ChintamaniGems',
)


