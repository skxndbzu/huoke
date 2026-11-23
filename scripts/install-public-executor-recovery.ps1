param(
    [ValidatePattern('^http://127\.0\.0\.1:[1-9][0-9]{0,4}$')]
    [string]$FeedProxy
)

$ErrorActionPreference = 'Stop'
$taskName = 'Huoke Public Collector Recovery'
$launcher = Join-Path $PSScriptRoot 'start-public-executors.ps1'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Project Python environment missing at $pythonPath"
}

$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$command = "& '" + $launcher.Replace("'", "''") + "' -Platform v2ex,nodeseek,github_discussions"
if ($FeedProxy) { $command += " -FeedProxy '$FeedProxy'" }
$windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $windowsPowerShell `
    -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -Command "' + $command + '"') `
    -WorkingDirectory $projectRoot
$triggers = @(
    New-ScheduledTaskTrigger -AtLogOn -User $account
    New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(5)) -RepetitionInterval (New-TimeSpan -Minutes 5)
)
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Principal $principal `
    -Settings $settings -Description 'Restarts missing Huoke public feed collectors and their SSH tunnel.' -Force |
    Out-Null
Start-ScheduledTask -TaskName $taskName
