# status.ps1 — Estado do Bot Ofertas
$BASE = $PSScriptRoot

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  BOT OFERTAS — STATUS" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# Processos
$rastreador = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like "python*.exe" -and $_.CommandLine -like "*rastreador.py*--random*"
})
$startup = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like "python*.exe" -and $_.CommandLine -like "*startup.py*"
})
$wa = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in "WhatsApp.exe","WhatsApp.Root.exe"
})

Write-Host "`nProcessos:" -ForegroundColor Yellow
if ($rastreador) { Write-Host "  Rastreador: RODANDO (PID $($rastreador[0].ProcessId))" -ForegroundColor Green }
else { Write-Host "  Rastreador: PARADO" -ForegroundColor Red }
if ($startup) { Write-Host "  Startup:    RODANDO (PID $($startup[0].ProcessId))" -ForegroundColor Green }
else { Write-Host "  Startup:    PARADO" -ForegroundColor DarkGray }
if ($wa) { Write-Host "  WhatsApp Desktop: RODANDO" -ForegroundColor Green }
else { Write-Host "  WhatsApp Desktop: NÃO ENCONTRADO" -ForegroundColor Red }

# Healthcheck
Write-Host "`nHealthcheck (http://127.0.0.1:8724/health):" -ForegroundColor Yellow
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:8724/health" -TimeoutSec 3
    $waStatus = if ($h.whatsapp.ok) { "OK ($($h.whatsapp.metodo))" } else { "OFF ($($h.whatsapp.motivo))" }
    Write-Host "  Telegram:   $($h.telegram.ok)" -ForegroundColor $(if($h.telegram.ok){"Green"}else{"Red"})
    Write-Host "  WhatsApp:   $waStatus" -ForegroundColor $(if($h.whatsapp.ok){"Green"}else{"Red"})
    Write-Host "  Rastreador: $($h.rastreador.ok)" -ForegroundColor $(if($h.rastreador.ok){"Green"}else{"Red"})
    Write-Host "  CPU: $($h.sistema.cpu)% | RAM: $($h.sistema.ram_pct)%"
}
catch {
    Write-Host "  Healthcheck OFF (bot não está rodando)" -ForegroundColor Red
}

# Últimos erros
Write-Host "`nÚltimos erros (data/errors.jsonl):" -ForegroundColor Yellow
$errFile = Join-Path $BASE "data\errors.jsonl"
if (Test-Path $errFile) {
    $tail = Get-Content $errFile -Tail 3
    if ($tail) {
        foreach ($line in $tail) {
            $e = $line | ConvertFrom-Json
            Write-Host "  [$($e.ts)] $($e.operacao): $($e.mensagem)" -ForegroundColor DarkGray
        }
    }
    else { Write-Host "  (nenhum)" -ForegroundColor Green }
}
else { Write-Host "  (nenhum)" -ForegroundColor Green }

# Última rodada
Write-Host "`nÚltima atividade (data/rastreador_local.log):" -ForegroundColor Yellow
$log = Join-Path $BASE "data\rastreador_local.log"
if (Test-Path $log) {
    $last = Get-Content $log -Tail 5 | Where-Object { $_ -match "Publicado|WhatsApp|Rodada" } | Select-Object -Last 3
    if ($last) { $last | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray } }
    else { Write-Host "  (sem eventos recentes)" -ForegroundColor DarkGray }
}
Write-Host ""
