param([switch]$CopyReviewToken)
$ErrorActionPreference = 'Stop'
$workbenchPort = 18080
if (-not (Get-NetTCPConnection -LocalPort $workbenchPort -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath ssh -WindowStyle Hidden -ArgumentList @('-N','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=30','-L','18080:127.0.0.1:18080','admin@117.72.35.216')
    Start-Sleep -Seconds 2
}
if ($CopyReviewToken) {
    # This is an owner-operated login helper, not an agent approval mechanism.
    $reviewAccess = & ssh admin@117.72.35.216 "sed -n 's/^REVIEWER_TOKEN=//p' /home/admin/huoke-staging/.env"
    if ($LASTEXITCODE -ne 0 -or -not $reviewAccess) { throw 'Could not load owner access token.' }
    Set-Clipboard -Value $reviewAccess.Trim()
    Remove-Variable reviewAccess
    Write-Host '审核令牌已复制到剪贴板。请粘贴到登录框，登录后清空剪贴板。'
}
Start-Process 'http://127.0.0.1:18080'
