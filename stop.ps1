# stop.ps1 — Para o Bot Ofertas
# Uso:  .\stop.ps1              respeita uma rodada em andamento (Regra 10)
#       .\stop.ps1 -Forcar      para mesmo no meio de uma rodada
[CmdletBinding()]
param([switch]$Forcar)

$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot
Set-Location $BASE

$ehWindows = ($null -eq $IsWindows) -or $IsWindows
if (-not $ehWindows) {
    Write-Host "stop.ps1 e do Windows: no Linux quem para o bot e o deploy/botctl.sh (Regra 16)." -ForegroundColor Yellow
    exit 1
}

# ─── Rodada em andamento? ─────────────────────────────────────────────────
# Regra 10: sempre checar core.database.execucao_em_andamento() antes de
# desligar processo. Matar no meio de uma rodada deixa o claim do produto
# pendurado e a publicacao pela metade.
if (-not $Forcar) {
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $resposta = (& python -c "from core import database as db; print('SIM' if db.execucao_em_andamento() else 'NAO')" 2>&1 | Out-String).Trim()
        $codigo = $LASTEXITCODE
    }
    catch { $resposta = ""; $codigo = 1 }
    finally { $ErrorActionPreference = $anterior }

    if ($codigo -eq 0 -and $resposta -match 'SIM') {
        Write-Host "Ha uma rodada de publicacao em andamento." -ForegroundColor Yellow
        Write-Host "Parar agora deixa o produto com claim pendurado (Regra 10)." -ForegroundColor Yellow
        Write-Host "Espere alguns minutos, ou rode:  .\stop.ps1 -Forcar" -ForegroundColor Yellow
        exit 2
    }
    if ($codigo -ne 0) {
        # Nao dar para checar nao pode virar "nao da para parar": o Daniel
        # precisa conseguir desligar o bot. Mas tambem nao se finge que
        # checou.
        Write-Host "AVISO: nao consegui checar se ha rodada em andamento; parando mesmo assim." -ForegroundColor Yellow
    }
}

# ─── Quem morre ───────────────────────────────────────────────────────────
# O startup.py vem PRIMEIRO de proposito: e o pai que relanca os filhos
# (Regra 10). Matando um filho antes do pai, o pai o sobe de novo e o stop
# nao para nada.
#
# Esta lista e a MESMA que o status.ps1 monitora. Antes aqui havia so
# `*rastreador.py*`, `*startup.py*` e `*setup_whatsapp*` — e
# "rastreador_amazon.py" NAO contem "rastreador.py", entao o rastreador da
# Amazon, a campanha de ferramentas e a fila do WhatsApp sobreviviam ao
# stop. O sintoma: `.\stop.ps1` dizia "Bot parado", o status.ps1 seguia
# mostrando 3 processos RODANDO e a Amazon continuava publicando.
$padroes = @(
    "*startup.py*",
    "*rastreador.py*",
    "*rastreador_amazon.py*",
    "*campanha_ferramentas.py*",
    "*whatsapp_queue_sender.py*",
    "*setup_whatsapp*"
)

$encerrados = 0
foreach ($padrao in $padroes) {
    $alvos = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
               Where-Object { $_.CommandLine -like $padrao })
    foreach ($p in $alvos) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  encerrado PID $($p.ProcessId) [$padrao]" -ForegroundColor DarkGray
        $encerrados++
    }
    # Um instante entre o pai e os filhos: sem isso, o filho ja lido na
    # lista pode ter sido substituido pelo pai antes de a lista terminar.
    if ($padrao -eq "*startup.py*" -and $alvos.Count -gt 0) { Start-Sleep -Milliseconds 800 }
}

# O Chrome dedicado do bot (perfil chrome_bot) — nunca o navegador do Daniel.
$chromes = @(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -like "*chrome_bot*" })
foreach ($p in $chromes) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "  encerrado PID $($p.ProcessId) [chrome_bot]" -ForegroundColor DarkGray
    $encerrados++
}

if ($encerrados -eq 0) {
    Write-Host "Bot ja estava parado." -ForegroundColor Yellow
    exit 0
}

# ─── Confere ──────────────────────────────────────────────────────────────
Start-Sleep -Seconds 2
$sobraram = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
              Where-Object { $cl = $_.CommandLine; ($padroes | Where-Object { $cl -like $_ }).Count -gt 0 })
if ($sobraram.Count -gt 0) {
    $pids = ($sobraram | ForEach-Object { $_.ProcessId }) -join ", "
    Write-Host "AVISO: ainda respondem os PID $pids. Rode .\stop.ps1 de novo." -ForegroundColor Yellow
    exit 1
}

Write-Host "Bot parado ($encerrados processo(s) encerrado(s))." -ForegroundColor Green
# O container da Evolution API tem restart: unless-stopped e segue no ar de
# proposito: e infraestrutura, nao o bot. Para derrubar tambem:
#   docker compose --env-file .env -f docker\evolution.yml stop
