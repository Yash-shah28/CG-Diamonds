param([switch]$SkipFreeze)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if (-not $SkipFreeze) {
        & .\.venv312\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist-next ChintamaniGems.spec
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
    }
    Copy-Item drc_sync\assets\chintamani-gems.ico dist-next\ChintamaniGems\ChintamaniGems.ico -Force
    $compiler = @("$env:LOCALAPPDATA\Programs\InnoSetup\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $compiler) { throw 'Install Inno Setup 6 or update the compiler path in packaging/build.ps1' }
    & $compiler packaging\installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
} finally { Pop-Location }
