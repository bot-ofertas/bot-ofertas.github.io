# agendar_shutdown.ps1
# Programa o ciclo diário do PC: desliga (suspende) às 02:00 aguardando o bot
# terminar o que estiver fazendo, e religa + sobe o bot às 08:30.
#
# Os horários NÃO ficam escritos aqui: vêm de `core/janela.py` (que lê
# HORA_LIGAR/HORA_DESLIGAR do .env). O watchdog do n8n, o supervisor e o
# relatório diário leem os mesmos valores — quando cada script carregava a
# sua cópia, mudar o horário exigia lembrar de cinco lugares, e esquecer de
# um deixava o watchdog alertando "bot caiu" toda madrugada.
#
# Uso:
#   .\agendar_shutdown.ps1                    → agenda
#   .\agendar_shutdown.ps1 -Status            → diagnóstico do ciclo
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

$TAREFAS = "BotOfertas-VerificacaoDiaria", "BotOfertas-Shutdown", "BotOfertas-WakeUp",
           "BotOfertas-Supervisor"

# ─── Horários: uma fonte só, em core/janela.py ────────────────────────────
# Se o Python não responder (venv quebrada, .env ilegível), cai nos padrões
# acordados em vez de abortar: um agendamento com os horários certos vale
# mais que nenhum agendamento.
$AGENDA = @{ ligar = "08:30"; desligar = "02:00"; verificacao = "01:00" }
try {
    $bruto = & python -m core.janela --agenda 2>$null
    if ($LASTEXITCODE -eq 0 -and $bruto) {
        $j = $bruto | ConvertFrom-Json
        if ($j.ligar)       { $AGENDA.ligar = $j.ligar }
        if ($j.desligar)    { $AGENDA.desligar = $j.desligar }
        if ($j.verificacao) { $AGENDA.verificacao = $j.verificacao }
    }
}
catch {
    Write-Host "  (nao consegui ler core/janela.py — usando horarios padrao)" -ForegroundColor DarkYellow
}

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

# Quantas tarefas nao consegui registrar. O instalador le o codigo de saida
# deste script para dizer se o ciclo ficou de pe; sem contar as falhas ele
# so poderia adivinhar.
$falhasAgenda = 0

# `(Get-Command python).Source` com $ErrorActionPreference = "Stop" derruba o
# script inteiro com um erro de .NET quando o python nao esta no PATH — e,
# pior, isso pode acontecer no meio, com metade das tarefas ja registradas e
# a outra metade nao. Melhor descobrir agora e dizer o que fazer.
$cmdPython = Get-Command python -ErrorAction SilentlyContinue
if (-not $cmdPython) {
    Write-Host "ERRO: 'python' nao esta no PATH deste usuario." -ForegroundColor Red
    Write-Host "      As tarefas agendadas rodam python direto, entao sem isso" -ForegroundColor Red
    Write-Host "      o ciclo diario nao teria como funcionar. Reinstale o Python" -ForegroundColor Red
    Write-Host "      marcando 'Add python.exe to PATH' e rode este script de novo." -ForegroundColor Red
    exit 1
}
$pythonExe = $cmdPython.Source

# Registrar-ScheduledTask falha (sem permissao, politica de grupo, nome em
# uso por outra conta) com erro terminante: sem este try/catch a primeira
# falha aborta o script e as tarefas seguintes nem sao tentadas, deixando o
# ciclo pela metade sem ninguem dizer qual metade.
function Registrar-Tarefa {
    param($Nome, $Params, $Ok)
    try {
        Register-ScheduledTask @Params -TaskName $Nome -Force -ErrorAction Stop | Out-Null
        Write-Host "  OK: $Ok" -ForegroundColor Green
    }
    catch {
        $script:falhasAgenda++
        Write-Host "  ERRO: nao registrei $Nome — $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "        (tente abrir o PowerShell como Administrador)" -ForegroundColor DarkGray
    }
}

# ─── 1. TAREFA DE VERIFICAÇÃO DIÁRIA — 01:00 (antes do shutdown) ─────────
Write-Host "[1/5] Agendando verificação diária às $($AGENDA.verificacao)..." -ForegroundColor Yellow

$pythonVerif = $pythonExe
$scriptVerif = Join-Path $BASE "verificacao_diaria.py"

$actionVerif = New-ScheduledTaskAction `
    -Execute $pythonVerif `
    -Argument "-u ""$scriptVerif""" `
    -WorkingDirectory $BASE

$triggerVerif = New-ScheduledTaskTrigger -Daily -At $AGENDA.verificacao

$settingsVerif = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
    # SEM -StartWhenAvailable — mesmo motivo do shutdown/wake (ver comentário abaixo).

$principalVerif = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME -LogonType Interactive

Registrar-Tarefa "BotOfertas-VerificacaoDiaria" @{
    Action = $actionVerif; Trigger = $triggerVerif
    Settings = $settingsVerif; Principal = $principalVerif
    Description = "Verifica saude do sistema e envia relatorio por Telegram antes do desligamento (Bot Ofertas)"
} "verificação diária agendada para $($AGENDA.verificacao)"

# ─── 2. TAREFA DE DESLIGAMENTO — 02:00 diariamente (aguarda se ocupado) ──
Write-Host "[2/5] Agendando desligamento diário às $($AGENDA.desligar) (aguarda até 35min se ocupado)..." -ForegroundColor Yellow

$scriptAguardar = Join-Path $BASE "aguardar_e_desligar.ps1"

$actionShut = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File ""$scriptAguardar""" `
    -WorkingDirectory $BASE

$triggerShut = New-ScheduledTaskTrigger -Daily -At $AGENDA.desligar

$settingsShut = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
    # SEM -StartWhenAvailable: essa flag faz o Windows "recuperar" o gatilho
    # perdido assim que o PC liga de novo, desligando fora de hora (bug real
    # encontrado em 2026-07-16 — PC desligava logo após ser ligado manualmente).

$principalShut = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME -LogonType Interactive

Registrar-Tarefa "BotOfertas-Shutdown" @{
    Action = $actionShut; Trigger = $triggerShut
    Settings = $settingsShut; Principal = $principalShut
    Description = "Desliga (suspende) o PC no fim da janela, aguardando ate 35min se o bot estiver ocupado (Bot Ofertas)"
} "desligamento agendado para $($AGENDA.desligar) (aguarda até 35min se ocupado)"

# ─── 3. TAREFA DE WAKE UP — no horario de religar (core/janela.py) ───────
Write-Host "[3/5] Agendando wake/inicio do bot às $($AGENDA.ligar)..." -ForegroundColor Yellow

# Chama acordar_e_iniciar.ps1 em vez do python direto: ele grava a linha
# "Wake OK" em data\shutdown.log antes de subir o bot, fechando o registro
# do ciclo. Sem isso o log só tinha a metade "Suspensao OK", e um PC que não
# voltava no horário não deixava pista de ONDE parou.
$scriptWake = Join-Path $BASE "acordar_e_iniciar.ps1"

$actionWake = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File ""$scriptWake""" `
    -WorkingDirectory $BASE

$triggerWake = New-ScheduledTaskTrigger -Daily -At $AGENDA.ligar

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

Registrar-Tarefa "BotOfertas-WakeUp" @{
    Action = $actionWake; Trigger = $triggerWake
    Settings = $settingsWake; Principal = $principalWake
    Description = "Acorda o PC no inicio da janela e inicia o bot (Bot Ofertas)"
} "wake/inicio agendado para $($AGENDA.ligar)"

# ─── 4. SUPERVISOR — de 30 em 30 min, garante o bot de pé ────────────────
Write-Host "[4/5] Agendando supervisor (a cada 30 min dentro da janela)..." -ForegroundColor Yellow

# Todo o ciclo diário depende de UMA tarefa acertar UM instante: às
# $($AGENDA.ligar) o WakeUp precisa acordar o PC e subir o bot. Se esse
# instante falha — queda de energia na madrugada, alguém desligou no botão,
# atualização do Windows engolindo o gatilho, startup.py morto às 11h —
# nao ha segunda chance e o dia inteiro passa sem publicar nos grupos
# (aconteceu em 31/07/2026, e o unico sintoma foi o PC ligado sem nada
# rodando). Repetindo a cada 30 min, "acertar um instante" vira "acertar
# qualquer instante do dia": no pior caso o bot volta sozinho meia hora
# depois. Quem decide se deve subir é o garantir_bot.py — ele nao faz nada
# fora da janela, com pausa ativa, ou com o bot ja rodando.
$pythonSup = $pythonExe
$scriptSup = Join-Path $BASE "garantir_bot.py"

$actionSup = New-ScheduledTaskAction `
    -Execute $pythonSup `
    -Argument "-u ""$scriptSup""" `
    -WorkingDirectory $BASE

# Repetição por 24h a partir de qualquer horário: a decisão de agir fica no
# Python (que conhece a janela), não no gatilho. Assim mudar HORA_LIGAR no
# .env não exige reagendar esta tarefa.
# Gatilho diário que se repete a cada 30 min por 24h — ou seja, sem parar,
# e re-armado todo dia. É o idioma clássico do Agendador porque não depende
# de `[TimeSpan]::MaxValue`, que já foi recusado por versões do Windows com
# "valor muito grande"; se isso acontecesse aqui a rede de segurança
# simplesmente não existiria, e ninguém perceberia até o dia em que ela
# fizesse falta. O fallback abaixo cobre o caso de `.Repetition` não poder
# ser copiada, e AVISA — melhor um supervisor pior do que um ausente.
$triggerSup = New-ScheduledTaskTrigger -Daily -At "00:05"
try {
    $modelo = New-ScheduledTaskTrigger -Once -At "00:05" `
        -RepetitionInterval (New-TimeSpan -Minutes 30) `
        -RepetitionDuration (New-TimeSpan -Hours 24)
    $triggerSup.Repetition = $modelo.Repetition
}
catch {
    Write-Host "  AVISO: nao consegui configurar a repeticao de 30 min." -ForegroundColor Yellow
    Write-Host "         O supervisor vai rodar 1x por dia (00:05) em vez de 48x." -ForegroundColor Yellow
    Write-Host "         Confira em: .\agendar_shutdown.ps1 -Status" -ForegroundColor DarkGray
}

# -StartWhenAvailable AQUI é correto, ao contrário das outras três: esta
# tarefa não tem hora marcada nem efeito destrutivo — recuperar uma execução
# perdida é exatamente o que se quer dela. Nas outras, recuperar significaria
# desligar o PC fora de hora (bug real de 2026-07-16).
$settingsSup = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

$principalSup = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME -LogonType Interactive

Registrar-Tarefa "BotOfertas-Supervisor" @{
    Action = $actionSup; Trigger = $triggerSup
    Settings = $settingsSup; Principal = $principalSup
    Description = "A cada 30 min: se o PC esta ligado dentro da janela e o bot nao esta rodando, sobe o processo pai (Bot Ofertas)"
} "supervisor a cada 30 min (só age dentro da janela)"

# ─── 4. Habilitar wake timers no Windows ─────────────────────────────────
Write-Host "[5/5] Habilitando wake timers do Windows..." -ForegroundColor Yellow
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
Write-Host "  $($AGENDA.verificacao) → Verificação diária (relatório de saúde por Telegram)" -ForegroundColor White
Write-Host "  $($AGENDA.desligar) → Desliga o PC (aguarda até 35min se o bot estiver ocupado)" -ForegroundColor White
Write-Host "  $($AGENDA.ligar) → Liga o PC + inicia o bot" -ForegroundColor White
Write-Host "  a cada 30min → Supervisor: se o PC está ligado na janela e o bot não," -ForegroundColor White
Write-Host "                 sobe o bot sozinho (rede de seguranca do ciclo)" -ForegroundColor White
Write-Host ""
Write-Host "IMPORTANTE:" -ForegroundColor Yellow
Write-Host "  Para o PC LIGAR sozinho às $($AGENDA.ligar), ele precisa estar em"
Write-Host "  SUSPENSAO/HIBERNACAO (nao desligado 100%)."
Write-Host ""
Write-Host "  O desligamento por padrao SUSPENDE (S3) — e so dessa forma o wake"
Write-Host "  timer religa o PC sozinho, sem depender da BIOS nem de senha."
Write-Host "  Desligamento completo (S5) so acorda com 'Wake on RTC' habilitado"
Write-Host "  na BIOS; sem isso o PC nao volta e o dia passa sem publicacao."
Write-Host ""
Write-Host "  Se o despertar falhar mesmo assim, o supervisor sobe o bot em ate"
Write-Host "  30 min depois que a maquina voltar — e o relatorio da manha avisa."
Write-Host ""
Write-Host "Conferir:       .\agendar_shutdown.ps1 -Status" -ForegroundColor DarkGray
Write-Host "Cancelar:       .\agendar_shutdown.ps1 -Remover" -ForegroundColor DarkGray

# Codigo de saida explicito. Sem ele, $LASTEXITCODE fica com o valor do
# ultimo executavel nativo que rodou (o powercfg, aqui, ou o que o
# instalador chamou antes) — e o instalador acabava anunciando "ciclo
# agendado" ou "agendar_shutdown.ps1 falhou" com base em lixo herdado.
if ($falhasAgenda -gt 0) {
    Write-Host "ATENCAO: $falhasAgenda tarefa(s) NAO foram registradas — o ciclo esta incompleto." -ForegroundColor Red
    exit 1
}
exit 0
