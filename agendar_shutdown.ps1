# agendar_shutdown.ps1
# Programa desligamento às 02:00 (aguarda até 35min se o bot estiver no meio
# de um ciclo) e ligar/iniciar bot às 08:45 diariamente.
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
Get-ScheduledTask -TaskName "BotOfertas-Shutdown","BotOfertas-WakeUp","BotOfertas-VerificacaoDiaria" `
    -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false

if ($Remover) {
    # Também tira permissão de wake timer
    powercfg -change -standby-timeout-ac 0 2>&1 | Out-Null
    Write-Host "Agendamentos removidos." -ForegroundColor Green
    exit 0
}

# ─── 1. TAREFA DE VERIFICAÇÃO DIÁRIA — 01:00 (antes do shutdown) ─────────
Write-Host "[1/4] Agendando verificação diária às 01:00..." -ForegroundColor Yellow

$pythonVerif = (Get-Command python).Source
$scriptVerif = Join-Path $BASE "verificacao_diaria.py"

$actionVerif = New-ScheduledTaskAction `
    -Execute $pythonVerif `
    -Argument "-u ""$scriptVerif""" `
    -WorkingDirectory $BASE

$triggerVerif = New-ScheduledTaskTrigger -Daily -At "01:00"

$settingsVerif = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
    # SEM -StartWhenAvailable — mesmo motivo do shutdown/wake (ver comentário abaixo).

$principalVerif = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName "BotOfertas-VerificacaoDiaria" `
    -Action $actionVerif -Trigger $triggerVerif `
    -Settings $settingsVerif -Principal $principalVerif `
    -Description "Verifica saude do sistema e envia relatorio por Telegram as 01:00, antes do shutdown (Bot Ofertas)" `
    -Force | Out-Null

Write-Host "  OK: verificação diária agendada para 01:00" -ForegroundColor Green

# ─── 2. TAREFA DE DESLIGAMENTO — 02:00 diariamente (aguarda se ocupado) ──
Write-Host "[2/4] Agendando shutdown diário às 02:00 (aguarda até 35min se ocupado)..." -ForegroundColor Yellow

$scriptAguardar = Join-Path $BASE "aguardar_e_desligar.ps1"

$actionShut = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File ""$scriptAguardar""" `
    -WorkingDirectory $BASE

$triggerShut = New-ScheduledTaskTrigger -Daily -At "02:00"

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
    -Description "Desliga o PC as 02:00 (aguarda ate 35min se o bot estiver ocupado) (Bot Ofertas)" `
    -Force | Out-Null

Write-Host "  OK: shutdown agendado para 02:00 (aguarda até 35min se ocupado)" -ForegroundColor Green

# ─── 3. TAREFA DE WAKE UP — 08:45 diariamente ────────────────────────────
Write-Host "[3/4] Agendando wake/inicio do bot às 08:45..." -ForegroundColor Yellow

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

# ─── 4. Habilitar wake timers no Windows ─────────────────────────────────
Write-Host "[4/4] Habilitando wake timers do Windows..." -ForegroundColor Yellow
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
Write-Host "  01:00 → Verificação diária (relatório de saúde por Telegram)" -ForegroundColor White
Write-Host "  02:00 → Desliga o PC (aguarda até 35min se o bot estiver ocupado)" -ForegroundColor White
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
