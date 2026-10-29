param([int]$Port = 8765, [ValidateSet('cuda','cpu')][string]$Device = 'cuda')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'evaluation\.venv-models\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Prepare the local model environment first; see docs/本地模型联调.md' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'backend\.env'))) { throw 'Run scripts/setup-backend.ps1 first' }
& $python (Join-Path $projectRoot 'backend\run_local.py') --port $Port --device $Device
if ($LASTEXITCODE -ne 0) { throw 'Local backend failed; check model files, CUDA memory and port availability' }
