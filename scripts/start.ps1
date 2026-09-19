# Start the WISA platform locally (backend :8000 + frontend :5173).
#
#   pwsh -File .\scripts\start.ps1
#
# Optional: point the platform at a specific ML interpreter for model pipelines
# (see DELIVERY_GUIDE.md §6). Set these BEFORE running this script:
#   $env:WSP_LOCAL_CPU_PYTHON_PATH = "D:\path\to\env\python.exe"
#   $env:WSP_LOCAL_CPU_RUNTIME_REF  = "local:<family>:cpu:<generation>"
#   $env:WSP_RUNTIME_FAMILY        = "<family>"

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

$venvPython = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Error "Missing .venv. Run: pwsh -File .\scripts\setup.ps1"
}

$logs = Join-Path $repo "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

Write-Host "Starting backend on http://127.0.0.1:8000 ..."
Start-Process -FilePath $venvPython `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $repo `
    -RedirectStandardOutput (Join-Path $logs "backend.out.log") `
    -RedirectStandardError (Join-Path $logs "backend.err.log") `
    -WindowStyle Hidden

Write-Host "Starting frontend on http://127.0.0.1:5173 ..."
Start-Process -FilePath "cmd.exe" `
    -ArgumentList @("/c", "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort") `
    -WorkingDirectory (Join-Path $repo "frontend") `
    -RedirectStandardOutput (Join-Path $logs "frontend.out.log") `
    -RedirectStandardError (Join-Path $logs "frontend.err.log") `
    -WindowStyle Hidden

Start-Sleep -Seconds 5
try {
    $health = Invoke-RestMethod "http://127.0.0.1:8000/api/health" -TimeoutSec 5
    Write-Host "Backend health: $($health.status)"
} catch {
    Write-Warning "Backend did not answer yet; see logs\backend.err.log"
}

Write-Host ""
Write-Host "Open http://127.0.0.1:5173"
Write-Host "Logs: $logs"
Write-Host "Stop: Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'wisa|uvicorn app.main:app|vite' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
