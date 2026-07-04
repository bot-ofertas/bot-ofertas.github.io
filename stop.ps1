# stop.ps1 — Para o Bot Ofertas
$BASE = $PSScriptRoot

$ps = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*rastreador.py*" -or
    $_.CommandLine -like "*startup.py*" -or
    $_.CommandLine -like "*setup_whatsapp*" -or
    ($_.Name -eq "chrome.exe" -and $_.CommandLine -like "*chrome_bot*")
}

if (-not $ps) {
    Write-Host "Bot já está parado." -ForegroundColor Yellow
    exit 0
}

foreach ($p in $ps) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "  encerrado PID $($p.ProcessId) [$($p.Name)]" -ForegroundColor DarkGray
}
Start-Sleep -Seconds 2
Write-Host "Bot parado." -ForegroundColor Green
