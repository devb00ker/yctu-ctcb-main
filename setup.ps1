$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    $TaskPython = Get-Command py -ErrorAction SilentlyContinue
    if ($TaskPython) {
        & py -3.12 -m venv .venv
    } else {
        & python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 x64, then run setup.ps1 again.' }
}
& .\.venv\Scripts\python.exe -m pip install --timeout 120 -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& .\.venv\Scripts\python.exe scripts\generate_proto.py
if ($LASTEXITCODE -ne 0) { throw 'Protocol generation failed.' }
Write-Host 'Ready. Run demo.bat or start.bat.'
