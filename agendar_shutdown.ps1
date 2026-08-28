# agendar_shutdown.ps1
# Programa desligamento às 02:00 (aguarda até 35min se o bot estiver no meio
# de um ciclo) e ligar/iniciar bot às 08:45 diariamente.
#
# Uso:
#   .\agendar_shutdown.ps1                    → agenda
#   .\agendar_shutdown.ps1 -Remover           → cancela agendamento

param(
    [switch]$Remover,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot

# Este script só faz sentido no Windows (Agendador de Tarefas + powercfg).
# Sem esta guarda, rodá-lo por engano em outro shell despeja uma parede de
# "termo não reconhecido" — `-ErrorAction SilentlyContinue` não silencia erro
# de resolução de comando — em vez de dizer o óbvio numa linha.
$ehWindows = ($null -eq $IsWindows) -or $IsWindows
if (-not $ehWindows) {
    Write-Host "Este script precisa do Windows (Agendador de Tarefas e powercfg)." -ForegroundColor Red
    exit 1
}

$TAREFAS = "BotOfertas-VerificacaoDiaria", "BotOfertas-Shutdown", "BotOfertas-WakeUp"

# ─── -Status: diagnóstico do ciclo diário ────────────────────────────────
# Existe porque este agendamento já falhou EM SILÊNCIO (31/07/2026: nem o
# desligamento nem o despertar rodaram, e o único sintoma foi o PC ligado de
# manhã). Sem ver LastRunTime/LastTaskResult e o estado dos wake timers, não
# há como saber se o problema é a tarefa, o wake timer ou a BIOS.
if ($Status) {
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host "  CICLO DIARIO — DESLIGA / RELIGA" -ForegroundColor Cyan
    Write-Host "==============================================" -ForegroundColor Cyan

    # 0 = sucesso; 267009 = rodando agora; 267011 = nunca executada.
    # Sem traduzir isso, o número sozinho não diz nada a quem está olhando.
    $codigos = @{
        0      = "OK"
        267009 = "rodando agora"
        267011 = "nunca executada"
        267014 = "encerrada pelo usuario"
        2147942402 = "arquivo nao encontrado (caminho errado?)"
    }

    Write-Host "`nTarefas agendadas:" -ForegroundColor Yellow
    foreach ($nome in $TAREFAS) {
        $t = Get-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue
        if (-not $t) {
            Write-Host "  $nome" -ForegroundColor Red
            Write-Host "      NAO REGISTRADA — rode .\agendar_shutdown.ps1" -ForegroundColor Red
            continue
        }
        $i = Get-ScheduledTaskInfo -TaskName $nome -ErrorAction SilentlyContinue
        $res = if ($null -ne $i) { $i.LastTaskResult } else { $null }
        $txt = if ($null -ne $res -and $codigos.ContainsKey([int]$res)) {
                   $codigos[[int]$res]
               } elseif ($null -ne $res) { "codigo $res" } else { "?" }
        $cor = if ($t.State -eq "Ready" -or $t.State -eq "Running") { "Green" } else { "Red" }
        Write-Host "  $nome  [$($t.State)]" -ForegroundColor $cor
        if ($i) {
            Write-Host "      ultima execucao : $($i.LastRunTime)  -> $txt"
            Write-Host "      proxima execucao: $($i.NextRunTime)"
        }
    }

    Write-Host "`nWake timers do Windows:" -ForegroundColor Yellow
    $wt = (powercfg -waketimers 2>&1 | Out-String).Trim()
    if ($wt -match "BotOfertas|Reserved|expira|expire") {
        ($wt -split "`n" | Where-Object { $_ -match "\S" }) | ForEach-Object {
            Write-Host "  $($_.Trim())" -ForegroundColor DarkGray
        }
    }
    else {
        Write-Host "  NENHUM wake timer ativo — o PC nao vai acordar sozinho." -ForegroundColor Red
        Write-Host "  Rode .\agendar_shutdown.ps1 para registrar de novo." -ForegroundColor DarkGray
    }

    Write-Host "`nComo o PC acordou da ultima vez:" -ForegroundColor Yellow
    ((powercfg -lastwake 2>&1 | Out-String) -split "`n" |
        Where-Object { $_ -match "\S" } | Select-Object -Last 4) |
        ForEach-Object { Write-Host "  $($_.Trim())" -ForegroundColor DarkGray }

    Write-Host "`nUltimos ciclos (data\shutdown.log):" -ForegroundColor Yellow
    $log = Join-Path $BASE "data\shutdown.log"
    if (Test-Path $log) {
        Get-Content $log -Tail 6 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    }
    else {
        Write-Host "  (ainda sem registro — o primeiro ciclo ainda nao aconteceu)" -ForegroundColor DarkGray
    }
    Write-Host ""
    exit 0
}

# ─── Remove agendamentos existentes ───────────────────────────────────────
Get-ScheduledTask -TaskName $TAREFAS -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false

if ($Remover) {
    # Não mexe em powercfg aqui: a chamada que estava neste ponto
    # (`-change -standby-timeout-ac 0`) LIGA o "nunca suspender", que é
    # exatamente o mesmo que a instalação faz — remover o agendamento
    # alterava a energia do PC no sentido oposto ao que o nome sugere.
    # Remover deve remover, e só.
    Write-Host "Agendamentos removidos (configuracao de energia intacta)." -ForegroundColor Green
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

# Chama acordar_e_iniciar.ps1 em vez do python direto: ele grava a linha
# "Wake OK" em data\shutdown.log antes de subir o bot, fechando o registro
# do ciclo. Sem isso o log só tinha a metade "Suspensao OK", e um PC que não
# voltava no horário não deixava pista de ONDE parou.
$scriptWake = Join-Path $BASE "acordar_e_iniciar.ps1"

$actionWake = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File ""$scriptWake""" `
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
Write-Host "Conferir:       .\agendar_shutdown.ps1 -Status" -ForegroundColor DarkGray
Write-Host "Cancelar:       .\agendar_shutdown.ps1 -Remover" -ForegroundColor DarkGray
