# agendar_shutdown.ps1
# Programa desligamento às 01:55 e ligar/iniciar bot às 08:45 diariamente.
#
# Uso:
#   .\agendar_shutdown.ps1                    → agenda
#   .\agendar_shutdown.ps1 -Remover           → cancela agendamento

param(
    [switch]$Remover
)

$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot

# ─── Remove agendamentos existentes ───────────────────────────────────────
Get-ScheduledTask -TaskName "BotOfertas-Shutdown","BotOfertas-WakeUp" `
    -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false

if ($Remover) {
    # Também tira permissão de wake timer
    powercfg -change -standby-timeout-ac 0 2>&1 | Out-Null
    Write-Host "Agendamentos removidos." -ForegroundColor Green
    exit 0
}

# ─── 1. TAREFA DE DESLIGAMENTO — 01:55 diariamente ───────────────────────
Write-Host "[1/3] Agendando shutdown diário às 01:55..." -ForegroundColor Yellow

$actionShut = New-ScheduledTaskAction `
    -Execute "shutdown.exe" `
    -Argument "/s /t 60 /c ""Desligamento programado — Bot Ofertas"""

$triggerShut = New-ScheduledTaskTrigger -Daily -At "01:55"

$settingsShut = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
    # SEM -StartWhenAvailable: essa flag faz o Windows "recuperar" o gatilho
    # perdido assim que o PC liga de novo, desligando fora de hora (bug real
    # encontrado em 2026-07-16 — PC desligava logo após ser ligado manualmente).

$principalShut = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName "BotOfertas-Shutdown" `
    -Action $actionShut -Trigger $triggerShut `
    -Settings $settingsShut -Principal $principalShut `
    -Description "Desliga o PC as 01:55 (Bot Ofertas)" `
    -Force | Out-Null

Write-Host "  OK: shutdown agendado para 01:55" -ForegroundColor Green

# ─── 2. TAREFA DE WAKE UP — 08:45 diariamente ────────────────────────────
Write-Host "[2/3] Agendando wake/inicio do bot às 08:45..." -ForegroundColor Yellow

$python = (Get-Command python).Source
$scriptStart = Join-Path $BASE "startup.py"

$actionWake = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "-u `"$scriptStart`"" `
    -WorkingDirectory $BASE

$triggerWake = New-ScheduledTaskTrigger -Daily -At "08:45"

# WakeToRun = tira o PC do sleep/hibernate no horário
# SEM -StartWhenAvailable (mesmo motivo do shutdown — evita disparo fora de hora)
$settingsWake = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

$principalWake = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName "BotOfertas-WakeUp" `
    -Action $actionWake -Trigger $triggerWake `
    -Settings $settingsWake -Principal $principalWake `
    -Description "Acorda o PC às 08:45 e inicia o bot (Bot Ofertas)" `
    -Force | Out-Null

Write-Host "  OK: wake/inicio agendado para 08:45" -ForegroundColor Green

# ─── 3. Habilitar wake timers no Windows ─────────────────────────────────
Write-Host "[3/3] Habilitando wake timers do Windows..." -ForegroundColor Yellow
powercfg -change -standby-timeout-ac 0 2>&1 | Out-Null
powercfg -setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>&1 | Out-Null
powercfg -setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>&1 | Out-Null
powercfg -SetActive SCHEME_CURRENT 2>&1 | Out-Null
Write-Host "  OK: wake timer habilitado" -ForegroundColor Green

# ─── Resumo ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  AGENDAMENTO ATIVO" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  01:55 → Desliga o PC" -ForegroundColor White
Write-Host "  08:45 → Liga o PC + inicia o bot" -ForegroundColor White
Write-Host ""
Write-Host "IMPORTANTE:" -ForegroundColor Yellow
Write-Host "  Para o PC LIGAR sozinho às 08:45, ele precisa estar em"
Write-Host "  SUSPENSAO/HIBERNACAO (nao desligado 100%)."
Write-Host ""
Write-Host "  O shutdown por padrao apenas suspende — o wake timer funciona."
Write-Host "  Se preferir desligamento COMPLETO (mais economico), precisa"
Write-Host "  configurar 'Wake on RTC' na BIOS."
Write-Host ""
Write-Host "Para cancelar:  .\agendar_shutdown.ps1 -Remover" -ForegroundColor DarkGray
Write-Host "Para ver:       Get-ScheduledTask 'BotOfertas-*'" -ForegroundColor DarkGray
