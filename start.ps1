# start.ps1 — Inicia o Bot Ofertas em segundo plano
$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot
Set-Location $BASE

# Se já está rodando, avisa
$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*rastreador.py*--random*" -or
    $_.CommandLine -like "*startup.py*"
}
if ($existing) {
    Write-Host "Bot já está rodando (PID $($existing[0].ProcessId))" -ForegroundColor Yellow
    exit 0
}

$python = (Get-Command python).Source
$log = Join-Path $BASE "data\startup_full.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

# Inicia startup.py destacado do PowerShell
Start-Process -FilePath $python `
    -ArgumentList "-u", "startup.py" `
    -WorkingDirectory $BASE `
    -RedirectStandardOutput $log `
    -RedirectStandardError "$log.err" `
    -WindowStyle Hidden

Start-Sleep -Seconds 6
Write-Host "Bot iniciado. Ver logs: .\logs.ps1" -ForegroundColor Green
Write-Host "Status: .\status.ps1" -ForegroundColor Green
