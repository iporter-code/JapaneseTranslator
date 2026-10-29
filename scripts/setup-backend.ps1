$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot 'backend'
Push-Location $backendRoot
try {
    if (-not (Test-Path '.venv\Scripts\python.exe')) { python -m venv .venv }
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    if (-not (Test-Path '.env')) {
        $example = Get-Content '.env.example' -Raw
        $randomBytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($randomBytes)
        $tokenValue = [Convert]::ToBase64String($randomBytes)
        $example.Replace('replace-with-a-long-random-token', $tokenValue) | Set-Content '.env' -Encoding UTF8
    }
    Write-Output 'Backend ready. The private access token is saved in backend/.env; do not share it publicly.'
} finally { Pop-Location }
