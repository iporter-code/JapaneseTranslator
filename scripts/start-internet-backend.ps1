param([ValidateRange(1,65535)][int]$Port = 8765, [switch]$SelfTest, [switch]$CheckOnly,
    [switch]$Background, [string]$TestOutput = 'streaming-internet.json')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'evaluation\.venv-models\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Prepare the local model environment first' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'backend\.env'))) { throw 'Run scripts/setup-backend.ps1 first' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.tools\cloudflared\cloudflared.exe'))) {
    & $python (Join-Path $projectRoot 'scripts\prepare-cloudflared.py')
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the portable tunnel client' }
}
$launchArguments = @(('"' + (Join-Path $projectRoot 'backend\run_internet.py') + '"'), '--port', $Port,
    '--test-output', ('"' + $TestOutput + '"'))
if ($SelfTest) { $launchArguments += '--self-test' }
if ($CheckOnly) { $launchArguments += '--check-only' }
if ($Background) {
    if ($CheckOnly) { throw 'Use -CheckOnly without -Background' }
    $launchId = [Guid]::NewGuid().ToString('N')
    $launchArguments += @('--launch-id', $launchId)
    $stdout = Join-Path $projectRoot ".tools\internet-$launchId.stdout.log"
    $stderr = Join-Path $projectRoot ".tools\internet-$launchId.stderr.log"
    $process = Start-Process -FilePath $python -ArgumentList $launchArguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $deadline = (Get-Date).AddSeconds(110)
    $statePath = Join-Path $projectRoot '.tools\internet-endpoint.json'
    while ((Get-Date) -lt $deadline) {
        if (Test-Path -LiteralPath $statePath) {
            $info = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            if ($info.launch_id -eq $launchId -and $info.ready) {
                Write-Output "Phone server address: $($info.origin)"
                Write-Output 'Background service is ready. Stop it with scripts/stop-internet-backend.ps1.'
                return
            }
        }
        if ($process.HasExited) { throw "Background startup failed. See $stderr" }
        Start-Sleep -Milliseconds 500
    }
    throw "Startup did not report ready in time. See $stderr"
}
# Passing an array invokes Python directly without re-parsing quotes.
$launchArguments[0] = Join-Path $projectRoot 'backend\run_internet.py'
$launchArguments[4] = $TestOutput
& $python @launchArguments
if ($LASTEXITCODE -ne 0) { throw 'Internet model backend failed; read the error above' }
