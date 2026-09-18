# start.ps1 — Inicia o Bot Ofertas em segundo plano
# Sobe o processo PAI (startup.py), que abre os rastreadores como filhos.
# Nunca subir os filhos direto: reiniciar so eles esgota o contador de
# tentativas do supervisor e derruba o bot inteiro em silencio (Regra 10).
$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot
Set-Location $BASE

# Get-CimInstance so existe no Windows, e -ErrorAction SilentlyContinue NAO
# silencia "termo nao reconhecido" (isso e erro de resolucao de comando).
# $IsWindows nao existe no Windows PowerShell 5.1: ali e $null, e nesse caso
# estamos necessariamente no Windows. Mesmo idioma do status.ps1.
$ehWindows = ($null -eq $IsWindows) -or $IsWindows
if (-not $ehWindows) {
    Write-Host "start.ps1 e do Windows: quem sobe o bot no Linux e o deploy/ (Regra 16)." -ForegroundColor Yellow
    exit 1
}

# ─── Pre-requisitos ───────────────────────────────────────────────────────
$cmdPython = Get-Command python -ErrorAction SilentlyContinue
if (-not $cmdPython) {
    Write-Host "ERRO: Python nao esta no PATH. Rode .\install.ps1 primeiro." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $BASE ".env"))) {
    # Sem .env o startup.py sai em ~1s. Dizer isso aqui e melhor do que
    # anunciar "bot iniciado" e deixar o motivo so no log.
    Write-Host "ERRO: .env nao existe. Rode .\install.ps1 primeiro." -ForegroundColor Red
    exit 1
}

# ─── Ja esta rodando? ─────────────────────────────────────────────────────
# Uma consulta so, filtrada no proprio WMI. A versao anterior enumerava
# TODOS os processos da maquina para depois filtrar no PowerShell.
$procsPython = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue)
$existente = @($procsPython | Where-Object {
    $_.CommandLine -like "*startup.py*" -or $_.CommandLine -like "*rastreador.py*"
})
if ($existente.Count -gt 0) {
    $pids = ($existente | ForEach-Object { $_.ProcessId }) -join ", "
    Write-Host "Bot ja esta rodando (PID $pids). Use .\status.ps1 ou .\stop.ps1." -ForegroundColor Yellow
    exit 0
}

# ─── Sobe ─────────────────────────────────────────────────────────────────
$log = Join-Path $BASE "data\startup_full.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

# Start-Process TRUNCA o arquivo de redirecionamento. Guardar a rodada
# anterior evita perder a evidencia justamente no caso de tentar duas vezes
# seguidas e querer ver o erro da primeira.
foreach ($arquivo in @($log, "$log.err")) {
    if (Test-Path $arquivo) { Move-Item $arquivo "$arquivo.anterior" -Force }
}

Start-Process -FilePath $cmdPython.Source `
    -ArgumentList "-u", "startup.py" `
    -WorkingDirectory $BASE `
    -RedirectStandardOutput $log `
    -RedirectStandardError "$log.err" `
    -WindowStyle Hidden

# ─── Confirma que subiu de verdade ────────────────────────────────────────
# "Chamar Popen nao e o mesmo que ter subido" (Regra 15): com .env invalido
# o startup.py sai em ~1s. O script antigo dormia 6s e imprimia "Bot
# iniciado" em verde de qualquer jeito — inclusive quando nao havia mais
# processo nenhum.
Write-Host "Subindo o startup.py..." -ForegroundColor DarkGray
$vivo = $false
foreach ($tentativa in 1..8) {
    Start-Sleep -Seconds 2
    $vivo = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
              Where-Object { $_.CommandLine -like "*startup.py*" }).Count -gt 0
    if (-not $vivo) { break }   # morreu: nao ha o que esperar
}

if (-not $vivo) {
    Write-Host "`nERRO: o startup.py subiu e morreu. Ultimas linhas do log:" -ForegroundColor Red
    foreach ($arquivo in @("$log.err", $log)) {
        if (Test-Path $arquivo) {
            $ultimas = Get-Content $arquivo -Tail 12 -ErrorAction SilentlyContinue
            if ($ultimas) {
                Write-Host "  --- $(Split-Path $arquivo -Leaf) ---" -ForegroundColor DarkGray
                $ultimas | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
            }
        }
    }
    Write-Host "`nConfira a configuracao com: python -m core.healthcheck" -ForegroundColor Yellow
    exit 1
}

# O /health sobe na etapa 3 do startup.py, depois do Chrome e do WhatsApp:
# pode demorar mais que a subida do processo. Nao responder aqui nao e
# falha — o processo esta vivo e o status.ps1 e quem acompanha.
$saude = "ainda subindo (veja .\status.ps1)"
foreach ($tentativa in 1..10) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:8724/health" -TimeoutSec 2 | Out-Null
        $saude = "/health respondendo"
        break
    }
    catch {
        # 503 e resposta: o healthcheck esta no ar e reprovando um
        # componente critico — nao e "nao subiu".
        if ($_.Exception.Response) { $saude = "/health respondendo (503, veja .\status.ps1)"; break }
    }
    Start-Sleep -Seconds 2
}

Write-Host "`nBot iniciado - $saude" -ForegroundColor Green
Write-Host "  .\status.ps1    estado completo"
Write-Host "  .\logs.ps1      log ao vivo"
Write-Host "  .\stop.ps1      parar"
