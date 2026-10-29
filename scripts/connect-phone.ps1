param([string]$Adb = 'D:\Android\android-sdk\platform-tools\adb.exe', [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
& $Adb devices
& $Adb reverse "tcp:$Port" "tcp:$Port"
if ($LASTEXITCODE -ne 0) { throw 'Connect and authorize a phone with USB debugging enabled first' }
Write-Output "In the debug app use http://127.0.0.1:$Port and the access token from backend/.env."
