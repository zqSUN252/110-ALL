param([int]$Port=8011,[string]$Profile='')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
if(-not $Profile){$Profile=Join-Path $projectRoot 'private\supplied-snapshot-runtime.json'}
if(-not (Test-Path -LiteralPath $Profile)){throw 'Verified local runtime profile unavailable'}
$env:MCSTAR_CONFIG=$Profile
$env:PYTHONPATH=Join-Path $projectRoot 'backend'
& (Join-Path $projectRoot '.venv\Scripts\python.exe') -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port $Port
