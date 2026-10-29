# VS Production Manager - one-time setup on a Windows PC.
# Run from the project folder in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
# Options: -SkipTests   skip the automated test suite at the end
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-Step([string]$Title, [scriptblock]$Block) {
    Write-Host "== $Title ==" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    & $Block
    if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) { throw "Step failed: $Title (exit code $LASTEXITCODE)" }
}

# Windows often redirects Documents/Desktop into OneDrive. The local database must not be synced.
$here = (Get-Location).Path
if ($here -match "OneDrive|Dropbox|Google Drive") {
    Write-Warning "This folder ($here) is synchronised by a cloud service. Move the project to a local folder such as C:\VSPM before using it in production."
}

# Find a suitable Python (3.11+): the py launcher first, then python on PATH.
$python = $null
foreach ($candidate in @(@("py", "-3.12"), @("py", "-3.13"), @("py", "-3.11"), @("python"))) {
    $exe = $candidate[0]; $args0 = @($candidate | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $out = & $exe @args0 -c "import sys; print('%d.%d' % sys.version_info[:2]); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $python = @($exe) + $args0; Write-Host "Using Python $out ($($python -join ' '))"; break }
    } catch { }
}
if (-not $python) { throw "Python 3.11 or newer was not found. Install Python 3.12 from python.org and tick 'Add python.exe to PATH'." }
$pyExe = $python[0]; $pyArgs = @($python | Select-Object -Skip 1)

if (-not (Test-Path .venv\Scripts\python.exe)) {
    Invoke-Step "Creating virtual environment (.venv)" { & $pyExe @pyArgs -m venv .venv }
}
$venvPy = ".\.venv\Scripts\python.exe"
Invoke-Step "Upgrading pip" { & $venvPy -m pip install --upgrade pip }
Invoke-Step "Installing VS Production Manager" { & $venvPy -m pip install -e ".[ai,google,windows,dev]" }

Write-Host "== Configuration ==" -ForegroundColor Cyan
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example - edit it before going live." -ForegroundColor Yellow
}
if (-not (Test-Path config\customers.yaml)) {
    Copy-Item config\customers.example.yaml config\customers.yaml
    Write-Host "Created config\customers.yaml - replace the example rules with your real customers." -ForegroundColor Yellow
}
New-Item -ItemType Directory -Force -Path data, credentials, reference_files | Out-Null

$env:PYTHONUTF8 = "1"
Invoke-Step "Local database and configuration check" { & .\.venv\Scripts\vspm.exe init }
if ((Test-Path "reference_files\*.xlsx") -and -not (Test-Path "data\test_workbook\*.xlsx")) {
    Invoke-Step "Creating the test copy of the workbook" { & .\.venv\Scripts\vspm.exe make-test-workbook }
}
if (Test-Path "reference_files\*.label") {
    Invoke-Step "Analysing reference files" { & .\.venv\Scripts\vspm.exe report }
}
if (-not $SkipTests) {
    Invoke-Step "Running the automated tests" { & $venvPy -m pytest -q }
}

Write-Host "`nDone. Start the app with scripts\start_vspm.bat" -ForegroundColor Green
