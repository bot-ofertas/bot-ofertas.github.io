# start.ps1 — Inicia o Bot Ofertas em segundo plano
$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot
Set-Location $BASE

# Se já está rodando, avisa
$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*rastreador.py*--random*" -or
    $_.CommandLine -like "*startup.py*"
}
if ($existing) {
    Write-Host "Bot já está rodando (PID $($existing[0].ProcessId))" -ForegroundColor Yellow
    exit 0
}

# ── Pasta de problemas na Area de Trabalho ───────────────────────────────────
function Get-PastaProblemas($base) {
    # Quem decide o caminho e o core/execucao_log.py (uma fonte so) e o deixa
    # escrito em data\caminho_log_execucao.txt. Repetir aqui a busca pela Area
    # de Trabalho daria caminhos diferentes justamente nos casos que importam:
    # OneDrive assumindo a pasta, ou o Windows em portugues mostrando
    # "Area de Trabalho". O fallback abaixo so vale antes da primeira execucao,
    # quando o ponteiro ainda nao existe.
    $ponteiro = Join-Path $base "data\caminho_log_execucao.txt"
    if (Test-Path $ponteiro) {
        $linhas = @(Get-Content $ponteiro -Encoding UTF8 | Where-Object { $_.Trim() })
        if ($linhas.Count -ge 1 -and (Test-Path $linhas[0].Trim())) {
            return $linhas[0].Trim()
        }
    }
    return (Join-Path ([Environment]::GetFolderPath("Desktop")) "problemas de execução")
}

$python = (Get-Command python).Source
$log = Join-Path $BASE "data\startup_full.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

# Inicia startup.py destacado do PowerShell
Start-Process -FilePath $python `
    -ArgumentList "-u", "startup.py" `
    -WorkingDirectory $BASE `
    -RedirectStandardOutput $log `
    -RedirectStandardError "$log.err" `
    -WindowStyle Hidden

Start-Sleep -Seconds 6
Write-Host "Bot iniciado. Ver logs: .\logs.ps1" -ForegroundColor Green
Write-Host "Status: .\status.ps1" -ForegroundColor Green

# Pasta de problemas: criada aqui tambem para o Daniel encontra-la na mesa
# mesmo quando o bot nem chega a subir (import quebrado, .env invalido) --
# nesse caso e justamente o log que ele precisa abrir.
$pastaProblemas = Get-PastaProblemas $BASE
New-Item -ItemType Directory -Force -Path $pastaProblemas | Out-Null
Write-Host ""
Write-Host "Problemas de execucao: $pastaProblemas" -ForegroundColor Cyan
Write-Host "  log de execucao.txt  -> cada execucao, com erro e ponto da falha" -ForegroundColor DarkGray
Write-Host "  erros detalhados.txt -> cada erro com traceback completo" -ForegroundColor DarkGray
