param(
    [switch]$Once,
    [switch]$WatchTunnel,
    [ValidateSet('v2ex','nodeseek','github_discussions')][string[]]$Platform = @('v2ex','nodeseek')
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runnerPath = Join-Path $PSScriptRoot 'social-executor.py'
$logDir = Join-Path $projectRoot '.local\social-executor'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-ProjectTunnel {
    $listener = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 18081 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $false }
    $tunnel = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $listener.OwningProcess)
    if (-not $tunnel) { return $false }
    if ($tunnel.Name -ne 'ssh.exe' -or $tunnel.CommandLine -notmatch '\s127\.0\.0\.1:18081:127\.0\.0\.1:18080\s+admin@117\.72\.35\.216\s*$') {
        throw 'Port 18081 is occupied by another service. Refusing to send project credentials there.'
    }
    return $true
}

if ($WatchTunnel) {
    $mutex = New-Object System.Threading.Mutex($false, 'Local\HuokePublicTunnel18081')
    $ownsTunnel = $false
    try {
        try { $ownsTunnel = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $ownsTunnel = $true }
        if (-not $ownsTunnel) { return }
        $sshArgs = @('-N', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3', '-L', '127.0.0.1:18081:127.0.0.1:18080', 'admin@117.72.35.216')
        $sshError = Join-Path $logDir 'tunnel.log'
        $watchLog = Join-Path $logDir 'tunnel-watch.log'
        while ($true) {
            try {
                if (-not (Test-ProjectTunnel)) {
                    $ssh = Start-Process -FilePath 'ssh.exe' -ArgumentList $sshArgs -WindowStyle Hidden -PassThru -RedirectStandardError $sshError
                    Add-Content -LiteralPath $watchLog -Value ('{0:o} started SSH process {1}' -f (Get-Date), $ssh.Id)
                    $ssh.WaitForExit()
                    Add-Content -LiteralPath $watchLog -Value ('{0:o} SSH process {1} exited' -f (Get-Date), $ssh.Id)
                }
            } catch {
                Add-Content -LiteralPath $watchLog -Value ('{0:o} {1}' -f (Get-Date), $_.Exception.Message)
            }
            Start-Sleep -Seconds 5
        }
    } finally {
        if ($ownsTunnel) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
    return
}

# Start the watchdog even if an earlier launcher already has an SSH tunnel open.
$ready = Test-ProjectTunnel
$watchArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-WatchTunnel')
Start-Process -FilePath 'powershell.exe' -ArgumentList $watchArgs -WindowStyle Hidden | Out-Null
for ($attempt = 0; -not $ready -and $attempt -lt 20; $attempt++) {
    Start-Sleep -Seconds 1
    $ready = Test-ProjectTunnel
}
if (-not $ready) { throw 'SSH tunnel is not ready. The watchdog will retry. See .local/social-executor/tunnel-watch.log.' }

foreach ($selectedPlatform in ($Platform | Select-Object -Unique)) {
    $existingRunner = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.CommandLine -like ('*' + $runnerPath + '*') -and $_.CommandLine -match ('\s--platform\s+' + $selectedPlatform + '(?:\s|$)')
    } | Select-Object -First 1
    if ($existingRunner) { continue }
    $runnerArgs = @(('"' + $runnerPath + '"'), '--platform', $selectedPlatform, '--ssh-operator')
    if (-not $Once) { $runnerArgs += '--loop' }
    Start-Process -FilePath $pythonPath -ArgumentList $runnerArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir ($selectedPlatform + '.log')) -RedirectStandardError (Join-Path $logDir ($selectedPlatform + '.error.log')) | Out-Null
}
Write-Output 'The public feed tunnel and requested executors are running. See .local/social-executor/*.log.'
