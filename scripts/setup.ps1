# WISA delivery bootstrap (Windows PowerShell).
#
# Creates the backend virtualenv, installs backend + frontend dependencies, and
# prints what local Python interpreters the platform can see. Safe to re-run.
#
#   pwsh -File .\scripts\setup.ps1

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

function Require-Command($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Error "'$name' is required but was not found. $hint"
    }
}

Require-Command python "Install Python 3.11+ and ensure 'python' is on PATH."
Require-Command npm "Install Node.js 18+ and ensure 'npm' is on PATH."

$pythonVersion = (python -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
Write-Host "Python: $pythonVersion"
if ([version]$pythonVersion -lt [version]"3.11") {
    Write-Error "Python 3.11+ is required (found $pythonVersion)."
}

$venv = Join-Path $repo ".venv"
if (-not (Test-Path -LiteralPath $venv)) {
    Write-Host "Creating virtualenv at $venv ..."
    python -m venv $venv
}

$venvPython = Join-Path $venv "Scripts\python.exe"
Write-Host "Installing backend dependencies ..."
& $venvPython -m pip install --upgrade pip | Out-Host
& $venvPython -m pip install -e ".\backend[dev]" | Out-Host

Write-Host "Installing frontend dependencies ..."
Push-Location (Join-Path $repo "frontend")
try { npm install | Out-Host } finally { Pop-Location }

if (-not (Test-Path -LiteralPath (Join-Path $repo "seed_data\mini-spacenet\test"))) {
    Write-Warning "seed_data/ is missing: the built-in Mini-SpaceNet dataset will not be registered."
}

Write-Host ""
Write-Host "=== Local Python environments the platform can see ==="
$env:PYTHONPATH = Join-Path $repo "backend"
& $venvPython -m app.cli runtime discover 2>&1 | Out-Host

Write-Host ""
Write-Host "Setup complete. Start the platform with:  pwsh -File .\scripts\start.ps1"
Write-Host "Then open http://127.0.0.1:5173"
