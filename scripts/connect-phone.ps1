param([string]$Adb = '', [int]$Port = 8765, [string]$Serial = '', [switch]$Install)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Adb) {
    $Adb = Join-Path $projectRoot '.tools\android-sdk\platform-tools\adb.exe'
    if (-not (Test-Path -LiteralPath $Adb)) { $Adb = 'adb' }
}
$devices = & $Adb devices
if ($LASTEXITCODE -ne 0) { throw 'ADB failed to enumerate devices' }
$available = @($devices | Where-Object { $_ -match '^\S+\s+device$' } | ForEach-Object { ($_ -split '\s+')[0] })
if ($Serial) {
    if ($Serial -notin $available) { throw 'The selected phone is not connected and authorized' }
} elseif ($available.Count -eq 1) { $Serial = $available[0] }
else { throw 'Connect exactly one authorized phone, or pass -Serial; enable USB debugging and accept the phone prompt' }
& $Adb -s $Serial reverse "tcp:$Port" "tcp:$Port"
if ($LASTEXITCODE -ne 0) { throw 'Connect and authorize a phone with USB debugging enabled first' }
if ($Install) {
    $apk = Join-Path $projectRoot 'artifacts\日语实时字幕-0.2.0-local.apk'
    if (-not (Test-Path -LiteralPath $apk)) { throw 'Build the current APK first' }
    & $Adb -s $Serial install -r $apk
    if ($LASTEXITCODE -ne 0) { throw 'APK installation failed; review the phone installation prompt' }
    & $Adb -s $Serial shell am start -n cn.jtranslate.live/.MainActivity
    if ($LASTEXITCODE -ne 0) { throw 'Could not open the installed app' }
}
Write-Output "In the debug app use http://127.0.0.1:$Port and the access token from backend/.env."
