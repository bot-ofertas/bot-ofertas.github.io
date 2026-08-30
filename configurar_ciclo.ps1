# configurar_ciclo.ps1 — liga o PC às 08:30 e desliga às 02:00, num comando só.
#
# Existe separado do instalar_tudo.ps1 de propósito: registrar o ciclo diário
# não deveria depender de o n8n estar configurado. Quem só quer a máquina
# ligando e desligando sozinha roda este; quem quer tudo roda o outro.
#
# O que ele faz, em ordem:
#   1. traz o código novo (o ciclo mora em core/janela.py + garantir_bot.py);
#   2. chama agendar_shutdown.ps1, que registra as 4 tarefas do Agendador;
#   3. mostra o -Status, que é a PROVA de que ficaram registradas.
#
# Uso:
#   .\configurar_ciclo.ps1            → atualiza o código e agenda
#   .\configurar_ciclo.ps1 -SemGit    → agenda com o código que já está no disco

param(
    [switch]$SemGit
)

$ErrorActionPreference = "Continue"
$BASE = $PSScriptRoot
Set-Location $BASE

$BRANCH = "claude/bot-ofertas-n8n-8d7qe2"

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  CICLO DIARIO — liga 08:30 / desliga 02:00" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# Este script só faz sentido no Windows (Agendador de Tarefas + powercfg).
# $IsWindows não existe no Windows PowerShell 5.1: ali ele é $null, e nesse
# caso estamos necessariamente no Windows.
$ehWindows = ($null -eq $IsWindows) -or $IsWindows
if (-not $ehWindows) {
    Write-Host "Este script precisa do Windows (Agendador de Tarefas e powercfg)." -ForegroundColor Red
    exit 1
}

# ─── 1. Código ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[1/3] Atualizando o codigo..." -ForegroundColor Yellow
if ($SemGit) {
    Write-Host "  pulado (-SemGit)" -ForegroundColor DarkGray
}
else {
    git fetch origin $BRANCH 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    git merge --ff-only FETCH_HEAD 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    if ($LASTEXITCODE -ne 0) {
        # `--ff-only` recusa em vez de criar merge automático: alteração local
        # no .env ou num script é a causa comum, e resolver isso no braço é
        # melhor que um merge silencioso no meio de uma instalação.
        Write-Host "  Nao consegui atualizar (alteracao local?). Seguindo com o codigo do disco." -ForegroundColor Yellow
        Write-Host "  Para ver o que trava:  git status" -ForegroundColor DarkGray
    }
}

# ─── 2. Agendamento ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/3] Registrando as tarefas no Agendador..." -ForegroundColor Yellow

# $LASTEXITCODE guarda o codigo do ULTIMO executavel nativo (o git, acima).
# Zerar antes e capturar o erro terminante aqui e o que faz o resultado
# abaixo dizer a verdade em vez de repetir lixo herdado.
$global:LASTEXITCODE = 0
try {
    & (Join-Path $BASE "agendar_shutdown.ps1")
    $ok = ($LASTEXITCODE -eq 0)
}
catch {
    Write-Host "  ERRO: $($_.Exception.Message)" -ForegroundColor Red
    $ok = $false
}

# ─── 3. Prova ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[3/3] Conferindo o que ficou registrado..." -ForegroundColor Yellow
& (Join-Path $BASE "agendar_shutdown.ps1") -Status

Write-Host ""
if ($ok) {
    Write-Host "PRONTO: o ciclo diario esta agendado." -ForegroundColor Green
    Write-Host ""
    Write-Host "  01:00  relatorio de saude por Telegram" -ForegroundColor White
    Write-Host "  02:00  suspende o PC (espera ate 35min se o bot estiver ocupado)" -ForegroundColor White
    Write-Host "  08:30  acorda o PC e sobe o bot" -ForegroundColor White
    Write-Host "  30/30min  supervisor: PC ligado na janela e bot fora do ar -> sobe o bot" -ForegroundColor White
    Write-Host ""
    Write-Host "Mudar os horarios: HORA_LIGAR / HORA_DESLIGAR no .env e rodar de novo." -ForegroundColor DarkGray
    exit 0
}
Write-Host "O agendamento NAO ficou completo — a lista acima mostra o que existe." -ForegroundColor Red
Write-Host "Tente abrir o PowerShell como Administrador e rodar de novo." -ForegroundColor Yellow
exit 1
