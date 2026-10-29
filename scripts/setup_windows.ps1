# VS Production Manager - one-time setup on a Windows PC.
# Run from the project folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "== Checking Python ==" -ForegroundColor Cyan
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { throw "Python launcher 'py' not found. Install Python 3.12 from python.org (tick 'Add to PATH')." }
& py -3.12 --version
if ($LASTEXITCODE -ne 0) { throw "Python 3.12 is required (py -3.12)." }

Write-Host "== Creating virtual environment (.venv) ==" -ForegroundColor Cyan
if (-not (Test-Path .venv)) { & py -3.12 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[ai,google,windows,dev]"

Write-Host "== Configuration ==" -ForegroundColor Cyan
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example - edit it before going live." -ForegroundColor Yellow
}
New-Item -ItemType Directory -Force -Path data, credentials, reference_files | Out-Null

Write-Host "== Database and checks ==" -ForegroundColor Cyan
& .\.venv\Scripts\vspm.exe init
if (Test-Path "reference_files\*.xlsx") {
    & .\.venv\Scripts\vspm.exe make-test-workbook
    & .\.venv\Scripts\vspm.exe report
}
& .\.venv\Scripts\python.exe -m pytest -q

Write-Host "`nDone. Start the app with scripts\start_vspm.bat" -ForegroundColor Green
