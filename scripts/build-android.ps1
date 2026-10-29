param([string]$SdkRoot = '', [string]$JavaHome = 'C:\Program Files\Java\jdk-21')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $SdkRoot) {
    $SdkRoot = Join-Path $projectRoot '.tools\android-sdk'
    if (-not (Test-Path $SdkRoot)) { $SdkRoot = $env:ANDROID_HOME }
}
if (-not (Test-Path (Join-Path $SdkRoot 'platforms\android-36\android.jar'))) { throw 'Install Android SDK Platform 36 first' }
if (-not (Test-Path (Join-Path $JavaHome 'bin\java.exe'))) { throw 'Pass -JavaHome with a compatible JDK location' }
$mappedDrive = $null
$oldJava = $env:JAVA_HOME
$oldGradle = $env:GRADLE_USER_HOME
try {
    $buildRoot = $projectRoot
    if ($projectRoot -match '[^\x00-\x7F]') {
        foreach ($letter in @('Z','Y','X','W','V','U','T')) {
            $candidate = "${letter}:"
            if (-not (Test-Path "$candidate\")) {
                & subst $candidate $projectRoot
                if ($LASTEXITCODE -ne 0) { throw 'Could not create temporary ASCII build path' }
                $mappedDrive = $candidate
                $buildRoot = "$candidate\"
                break
            }
        }
        if (-not $mappedDrive) { throw 'No free drive letter for the temporary build mapping' }
    }
    $sdkForBuild = $SdkRoot
    if ($mappedDrive -and $SdkRoot.StartsWith($projectRoot, [StringComparison]::OrdinalIgnoreCase)) {
        $sdkForBuild = $mappedDrive + $SdkRoot.Substring($projectRoot.Length)
    }
    $env:JAVA_HOME = $JavaHome
    $env:GRADLE_USER_HOME = Join-Path $buildRoot '.tools\gradle-home'
    Set-Content -LiteralPath (Join-Path $buildRoot 'android\local.properties') -Value ('sdk.dir=' + $sdkForBuild.Replace('\','/').Replace(':','\:')) -Encoding UTF8 -NoNewline
    $gradleCommand = Join-Path $buildRoot '.tools\gradle-8.13\bin\gradle.bat'
    if (-not (Test-Path $gradleCommand)) { $gradleCommand = Join-Path $buildRoot 'android\gradlew.bat' }
    & $gradleCommand -p (Join-Path $buildRoot 'android') assembleDebug testDebugUnitTest lintDebug --console=plain --no-daemon
    if ($LASTEXITCODE -ne 0) { throw 'Android build or checks failed' }
    $output = Join-Path $projectRoot 'artifacts'
    New-Item -ItemType Directory -Path $output -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot 'android\app\build\outputs\apk\debug\app-debug.apk') -Destination (Join-Path $output '日语实时字幕-0.3.1.apk') -Force
    Get-FileHash -LiteralPath (Join-Path $output '日语实时字幕-0.3.1.apk') -Algorithm SHA256
} finally {
    $env:JAVA_HOME = $oldJava
    $env:GRADLE_USER_HOME = $oldGradle
    if ($mappedDrive) { & subst $mappedDrive /D }
}
