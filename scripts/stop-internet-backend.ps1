$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot '.tools\internet-endpoint.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Output 'No recorded Internet backend'; return }
$info = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if (-not $info.ready) { Write-Output 'Recorded backend has stopped'; return }
if ($info.stop_name -notmatch '^internet-stop-\d+-[0-9a-f]{16}\.flag$') {
    throw 'This older backend has no safe stop channel; close its original terminal first'
}
$stopPath = Join-Path $projectRoot ('.tools\' + $info.stop_name)
# Only write a generated signal filename inside this workspace, never kill arbitrary PIDs.
Set-Content -LiteralPath $stopPath -Value 'stop' -Encoding ascii
$deadline = (Get-Date).AddSeconds(35)
while ((Get-Date) -lt $deadline) {
    $latest = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($latest.launch_id -ne $info.launch_id -or -not $latest.ready) { Write-Output 'Internet backend and tunnel stopped'; return }
    Start-Sleep -Milliseconds 500
}
throw 'Stop was requested but cleanup is still pending; inspect the backend log'
