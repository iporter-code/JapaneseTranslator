param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$backendRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend'
Push-Location $backendRoot
try {
    if (-not (Test-Path '.env')) { throw 'Run scripts/setup-backend.ps1 first' }
    & '.\.venv\Scripts\python.exe' -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port $Port --ws-max-size 16384
} finally { Pop-Location }
