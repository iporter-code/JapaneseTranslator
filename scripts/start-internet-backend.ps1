param([ValidateRange(1,65535)][int]$Port = 8765, [switch]$SelfTest, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'evaluation\.venv-models\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Prepare the local model environment first' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'backend\.env'))) { throw 'Run scripts/setup-backend.ps1 first' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.tools\cloudflared\cloudflared.exe'))) {
    & $python (Join-Path $projectRoot 'scripts\prepare-cloudflared.py')
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the portable tunnel client' }
}
$launchArguments = @((Join-Path $projectRoot 'backend\run_internet.py'), '--port', $Port)
if ($SelfTest) { $launchArguments += '--self-test' }
if ($CheckOnly) { $launchArguments += '--check-only' }
& $python @launchArguments
if ($LASTEXITCODE -ne 0) { throw 'Internet model backend failed; read the error above' }
