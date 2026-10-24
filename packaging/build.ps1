$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    python -m PyInstaller --noconfirm --clean DRCInventorySync.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
} finally { Pop-Location }
