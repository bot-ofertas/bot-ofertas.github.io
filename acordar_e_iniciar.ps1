# acordar_e_iniciar.ps1
# Ação da tarefa BotOfertas-WakeUp: registra que o PC acordou e sobe o bot
# pelo processo PAI.
#
# Existe para fechar o registro do ciclo diário. Antes, data\shutdown.log só
# tinha a metade "Suspensao OK" — quando o PC não voltava no horário, não
# havia nenhuma linha dizendo se o wake nem chegou a acontecer ou se ele
# aconteceu e o bot é que não subiu. São dois problemas diferentes, com
# consertos diferentes (wake timer/BIOS vs. Python/venv), e sem essa linha
# não dava para distinguir um do outro.

$ErrorActionPreference = "Continue"
$BASE = $PSScriptRoot
$logFile = Join-Path $BASE "data\shutdown.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

New-Item -ItemType Directory -Force -Path (Join-Path $BASE "data") | Out-Null

# `powercfg -lastwake` diz o que tirou a máquina do sono (Wake Timer, botão,
# teclado, rede). Se o PC acordou por outro motivo e a tarefa pegou carona,
# isso aparece aqui — e explica um "acordou fora de hora".
$motivo = try { (powercfg -lastwake 2>&1 | Out-String).Trim() } catch { "(nao consegui ler)" }
$motivo = ($motivo -split "`n" | Where-Object { $_ -match "\S" } | Select-Object -Last 3) -join " / "

Add-Content -Path $logFile -Value "$ts - Wake OK - $motivo"

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Add-Content -Path $logFile -Value "$ts - ERRO: python nao encontrado no PATH; bot NAO iniciado"
    exit 1
}

# Sobe o bot pelo supervisor, não por um Start-Process direto.
#
# O supervisor faz três checagens que este script não tem como fazer sozinho:
# se estamos dentro da janela de operação, se há pausa ativa, e se o bot já
# está rodando. A terceira é a que importa aqui: o despertar e a repetição de
# 30 minutos do BotOfertas-Supervisor podem cair no mesmo minuto, e dois
# `startup.py` subindo juntos significam dois conjuntos de rastreadores
# publicando a mesma oferta em paralelo. Com os dois passando pelo mesmo
# `rastreador_em_execucao()`, o segundo desiste.
#
# Ele também sobe o processo PAI (Regra 10 do CLAUDE.md — reiniciar só os
# filhos esgota o contador de tentativas do supervisor interno).
& $python -u (Join-Path $BASE "garantir_bot.py") 2>&1 |
    ForEach-Object { Add-Content -Path $logFile -Value "$ts   $_" }

Add-Content -Path $logFile -Value "$ts - Despertar concluido"
