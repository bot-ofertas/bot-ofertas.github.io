# install.ps1 — Instalador completo do Bot Ofertas via PowerShell
# Uso: .\install.ps1

$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot
Set-Location $BASE

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  BOT OFERTAS — INSTALAÇÃO COMPLETA" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ─── 1. Verifica Python ───────────────────────────────────────────────────
Write-Host "`n[1/6] Verificando Python..." -ForegroundColor Yellow
try {
    $pyVer = & python --version 2>&1
    Write-Host "  OK: $pyVer" -ForegroundColor Green
}
catch {
    Write-Host "  ERRO: Python não encontrado. Instale em https://python.org" -ForegroundColor Red
    exit 1
}

# ─── 2. Instala dependências pip ──────────────────────────────────────────
Write-Host "`n[2/6] Instalando dependências Python..." -ForegroundColor Yellow
& python -m pip install --upgrade pip --quiet
& python -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERRO no pip install" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: dependências instaladas" -ForegroundColor Green

# ─── 3. Instala navegador Playwright (opcional) ───────────────────────────
Write-Host "`n[3/6] Instalando browser Playwright (para scraping ML)..." -ForegroundColor Yellow
& python -m playwright install chromium 2>&1 | Out-Null
Write-Host "  OK: Chromium instalado" -ForegroundColor Green

# ─── 4. Verifica .env ─────────────────────────────────────────────────────
Write-Host "`n[4/6] Verificando .env..." -ForegroundColor Yellow
if (-not (Test-Path "$BASE\.env")) {
    Write-Host "  .env não encontrado — copiando de .env.example" -ForegroundColor Yellow
    if (Test-Path "$BASE\.env.example") {
        Copy-Item "$BASE\.env.example" "$BASE\.env"
        Write-Host "  ATENÇÃO: edite $BASE\.env com suas credenciais antes de rodar" -ForegroundColor Red
    }
    else {
        Write-Host "  Nenhum .env.example — crie o .env manualmente" -ForegroundColor Red
    }
}
else {
    Write-Host "  OK: .env encontrado" -ForegroundColor Green
}

# ─── 5. Cria pasta data ───────────────────────────────────────────────────
Write-Host "`n[5/6] Preparando pasta data\..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "$BASE\data" | Out-Null
Write-Host "  OK: $BASE\data" -ForegroundColor Green

# ─── 6. Registra tarefa Windows (auto-start no login) ────────────────────
Write-Host "`n[6/6] Registrando tarefa Windows (auto-start no login)..." -ForegroundColor Yellow
$taskName = "BotOfertas-AutoStart"
$python = (Get-Command python).Source
$scriptPath = Join-Path $BASE "startup.py"

$action = New-ScheduledTaskAction -Execute $python `
    -Argument "`"$scriptPath`"" -WorkingDirectory $BASE
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Bot Ofertas — Telegram + WhatsApp automático" `
    -Force | Out-Null

Write-Host "  OK: tarefa '$taskName' criada" -ForegroundColor Green

# ─── Conclusão ────────────────────────────────────────────────────────────
Write-Host "`n==============================================" -ForegroundColor Cyan
Write-Host "  INSTALAÇÃO CONCLUÍDA" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Próximos passos:" -ForegroundColor Yellow
Write-Host "  1. Verifique que .env está com TOKEN_TELEGRAM e CANAL_GERAL"
Write-Host "  2. Rode:   .\start.ps1        (inicia agora)"
Write-Host "  3. Rode:   .\status.ps1       (ver estado)"
Write-Host "  4. Rode:   .\logs.ps1         (ver logs)"
Write-Host "  5. Rode:   .\stop.ps1         (parar)"
Write-Host ""
Write-Host "WhatsApp API (opcional, precisa Docker):" -ForegroundColor Yellow
Write-Host "  docker compose -f docker\evolution.yml up -d"
Write-Host "  python setup_whatsapp_api.py"
Write-Host ""
