param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Push-Location $projectRoot
try {
    if (!(Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        & $Python -m venv .venv
        if ($LASTEXITCODE) { throw 'Python environment creation failed.' }
    }
    & '.venv/Scripts/python.exe' -m pip install -r backend/requirements.lock
    if ($LASTEXITCODE) { throw 'Locked Python dependency installation failed.' }
    Push-Location web
    try {
        & npm.cmd ci
        if ($LASTEXITCODE) { throw 'Locked web dependency installation failed.' }
        & npm.cmd run build
        if ($LASTEXITCODE) { throw 'Web build failed.' }
    } finally { Pop-Location }
} finally { Pop-Location }
