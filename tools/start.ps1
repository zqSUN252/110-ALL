param(
    [string]$Profile = 'private/supplied-snapshot-runtime.json',
    [ValidateRange(1024,65535)][int]$Port = 8011,
    [ValidateRange(30,600)][int]$StartupTimeoutSeconds = 180,
    [switch]$Background,
    [switch]$WithoutLocalJobs
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$profilePath = if ([IO.Path]::IsPathRooted($Profile)) {[IO.Path]::GetFullPath($Profile)} else {[IO.Path]::GetFullPath((Join-Path $projectRoot $Profile))}
if (!(Test-Path -LiteralPath $profilePath -PathType Leaf)) { throw 'Provide a controlled runtime profile. Setup never creates demonstration business data.' }
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Run tools/setup.ps1 first.' }
if (!(Test-Path -LiteralPath (Join-Path $projectRoot 'web/dist/index.html'))) { throw 'Build web assets with tools/setup.ps1 first.' }
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) { throw "Port $Port is already in use. Choose a free port; this launcher does not replace an existing process." }
$env:MCSTAR_CONFIG = $profilePath
$env:PYTHONPATH = Join-Path $projectRoot 'backend'
$env:PYTHONIOENCODING = 'utf-8'
$arguments = @('-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port',"$Port",'--no-access-log')
if (!$Background) {
    & $pythonPath @arguments
    exit $LASTEXITCODE
}
$receiptRoot = Join-Path $projectRoot ('private/processes/' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $receiptRoot -Force | Out-Null
$server = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $receiptRoot 'server.out.log') -RedirectStandardError (Join-Path $receiptRoot 'server.error.log')
$healthy = $false
$deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
while ([DateTime]::UtcNow -lt $deadline) {
    $server.Refresh()
    if ($server.HasExited) { throw ("Application process exited. Inspect {0}" -f $receiptRoot) }
    try { $response=Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/health" -f $Port) -TimeoutSec 1; if ($response.status -eq 'ok') {$healthy=$true;break} } catch {}
    Start-Sleep -Milliseconds 200
}
if (!$healthy) { throw ("Application did not become healthy. Inspect {0}" -f $receiptRoot) }
$jobsPid = $null
if (!$WithoutLocalJobs) {
    $jobs = Start-Process -FilePath $pythonPath -ArgumentList @('tools/local_jobs.py') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $receiptRoot 'jobs.out.log') -RedirectStandardError (Join-Path $receiptRoot 'jobs.error.log')
    $jobsPid = $jobs.Id
}
@{server_pid=$server.Id;local_jobs_pid=$jobsPid;port=$Port;profile_path=$profilePath;started_at=(Get-Date).ToUniversalTime().ToString('o');external_workers_started=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $receiptRoot 'receipt.json') -Encoding utf8
Write-Output ("Application: http://127.0.0.1:{0}; server PID {1}; process receipt: {2}" -f $Port,$server.Id,$receiptRoot)
