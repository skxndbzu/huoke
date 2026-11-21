param(
    [switch]$Once,
    [ValidateSet('v2ex','nodeseek','github_discussions')][string[]]$Platform = @('v2ex','nodeseek')
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runnerPath = Join-Path $PSScriptRoot 'social-executor.py'
$logDir = Join-Path $projectRoot '.local\social-executor'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
# Dedicated tunnel, not the workbench/browser helper. Never reads a reviewer token.
$listener = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 18081 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $tunnelProcess = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $listener[0].OwningProcess)
    if ($tunnelProcess.Name -ne 'ssh.exe' -or $tunnelProcess.CommandLine -notlike '*127.0.0.1:18081:127.0.0.1:18080*' -or $tunnelProcess.CommandLine -notlike '*admin@117.72.35.216*') {
        throw 'Port 18081 is occupied by another service. Refusing to send project credentials there.'
    }
}
if (-not $listener) {
    Start-Process -FilePath 'ssh.exe' -ArgumentList @('-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=20', '-L', '127.0.0.1:18081:127.0.0.1:18080', 'admin@117.72.35.216') -WindowStyle Hidden -RedirectStandardError (Join-Path $logDir 'tunnel.log') | Out-Null
    Start-Sleep -Seconds 2
    if (-not (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 18081 -State Listen -ErrorAction SilentlyContinue)) {
        throw 'SSH tunnel failed; inspect .local/social-executor/tunnel.log.'
    }
}
foreach ($selectedPlatform in $Platform) {
    $runnerArgs = @(('"' + $runnerPath + '"'), '--platform', $selectedPlatform, '--ssh-operator')
    if (-not $Once) { $runnerArgs += '--loop' }
    Start-Process -FilePath $pythonPath -ArgumentList $runnerArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir ($selectedPlatform + '.log')) -RedirectStandardError (Join-Path $logDir ($selectedPlatform + '.error.log')) | Out-Null
}
Write-Output 'Public feed executors started. See .local/social-executor/*.log. Per-platform locks prevent overlapping workers.'
