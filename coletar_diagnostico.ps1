# coletar_diagnostico.ps1 — junta o estado da instalacao num zip so.
#
# Serve para levar o diagnostico daqui para uma conversa/analise fora deste
# PC. O que ele NAO faz, de proposito:
#
#   - nao copia o .env. Ele so registra QUAIS chaves existem e se estao
#     preenchidas — o valor nunca sai do disco. Saber que
#     WHATSAPP_GROUP_NAME esta vazio resolve o problema; ver o token nao;
#   - nao copia data\ml_profile\ (perfil de navegador com a sessao logada do
#     Mercado Livre) nem arquivo com "token" no nome;
#   - redige o que sobrou. Os logs antigos TEM o token do Telegram dentro
#     (foi assim que ele vazou para o repositorio publico em 2026-09-07):
#     copiar o log cru so mudaria o vazamento de lugar.
#
# Uso:  .\coletar_diagnostico.ps1

$BASE = $PSScriptRoot
Set-Location $BASE

$destino = Join-Path ([Environment]::GetFolderPath("Desktop")) "bot_ofertas_diagnostico.zip"
$tmp = Join-Path $env:TEMP ("bot_diag_" + (Get-Date -Format "HHmmss"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

function Redigir($texto) {
    if (-not $texto) { return $texto }
    $texto = $texto -replace '\d{8,12}:AA[\w-]{30,}', '***REDIGIDO***'
    $texto = $texto -replace 'sk-ant-api\d{2}-[\w-]{20,}', '***REDIGIDO***'
    $texto = $texto -replace 'APP_USR-[\w-]{20,}', '***REDIGIDO***'
    $texto = $texto -replace 'TG-[\w-]{20,}', '***REDIGIDO***'
    return $texto
}

Write-Host "Coletando..." -ForegroundColor Cyan

# ── logs e dados (sem perfil de navegador, sem arquivo de token) ─────────
$alvos = @()
$alvos += Get-ChildItem -Path (Join-Path $BASE "data") -File -ErrorAction SilentlyContinue |
          Where-Object { $_.Name -notmatch 'token|cookie|session' -and $_.Length -lt 20MB }
$alvos += Get-ChildItem -Path $BASE -File -Filter "*.log" -ErrorAction SilentlyContinue

foreach ($f in $alvos) {
    $saida = Join-Path $tmp $f.Name
    if ($f.Extension -in @(".log", ".jsonl", ".json", ".txt", ".err")) {
        try { Redigir (Get-Content $f.FullName -Raw -ErrorAction Stop) | Set-Content $saida -Encoding UTF8 }
        catch { Copy-Item $f.FullName $saida -ErrorAction SilentlyContinue }
    }
    else { Copy-Item $f.FullName $saida -ErrorAction SilentlyContinue }
}
Write-Host "  $($alvos.Count) arquivo(s) de log/dados" -ForegroundColor DarkGray

# ── .env: so os NOMES das chaves e se estao preenchidas ─────────────────
$envPath = Join-Path $BASE ".env"
if (Test-Path $envPath) {
    $linhas = foreach ($l in (Get-Content $envPath)) {
        if ($l -match '^\s*#' -or $l -notmatch '=') { continue }
        $chave = ($l -split '=', 2)[0].Trim()
        $valor = ($l -split '=', 2)[1]
        if ([string]::IsNullOrWhiteSpace($valor)) { "$chave = (VAZIO)" }
        else { "$chave = (preenchido, $($valor.Trim().Length) caracteres)" }
    }
    $linhas | Set-Content (Join-Path $tmp "_env_CHAVES.txt") -Encoding UTF8
    Write-Host "  .env: $($linhas.Count) chaves (só os nomes)" -ForegroundColor DarkGray
}

# ── estado da maquina ───────────────────────────────────────────────────
$estado = @()
$estado += "coletado_em: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
$estado += "pasta: $BASE"
$estado += ""
$estado += "== git =="
$estado += "branch: " + (git rev-parse --abbrev-ref HEAD 2>&1)
$estado += "commit: " + (git rev-parse --short HEAD 2>&1)
$estado += "sujo:"
$estado += (git status --porcelain 2>&1)
$estado += ""
$estado += "== python =="
$estado += (python --version 2>&1)
$estado += ""
$estado += "== processos do bot =="
$estado += (Get-CimInstance Win32_Process |
            Where-Object { $_.CommandLine -like "*startup.py*" -or $_.CommandLine -like "*rastreador*.py*" } |
            ForEach-Object { "PID $($_.ProcessId): $($_.CommandLine)" })
$estado += ""
$estado += "== tarefas agendadas =="
$estado += (Get-ScheduledTask -TaskName "BotOfertas-*" -ErrorAction SilentlyContinue |
            ForEach-Object {
                $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
                "$($_.TaskName) | $($_.State) | ultima=$($i.LastRunTime) | resultado=$($i.LastTaskResult) | proxima=$($i.NextRunTime)"
            })
$estado += ""
$estado += "== healthcheck =="
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8724/health" -UseBasicParsing -TimeoutSec 5
    $estado += "HTTP $($r.StatusCode)"; $estado += $r.Content
}
catch {
    # /health responde 503 DE PROPOSITO quando alguma integracao esta ruim —
    # o corpo e o diagnostico, entao ler o corpo do erro importa mais que
    # registrar "falhou".
    $resp = $_.Exception.Response
    if ($resp) {
        $estado += "HTTP $([int]$resp.StatusCode)"
        try { $estado += (New-Object IO.StreamReader($resp.GetResponseStream())).ReadToEnd() } catch {}
    }
    else { $estado += "sem resposta: $($_.Exception.Message)" }
}
(Redigir ($estado -join "`r`n")) | Set-Content (Join-Path $tmp "_ESTADO.txt") -Encoding UTF8

# ── relatorio da Area de Trabalho ───────────────────────────────────────
foreach ($nome in @("problemas de execucao.txt", "Problemas de execução para corrigir.txt")) {
    $rel = Join-Path ([Environment]::GetFolderPath("Desktop")) $nome
    if (Test-Path $rel) {
        Redigir (Get-Content $rel -Raw) | Set-Content (Join-Path $tmp $nome) -Encoding UTF8
    }
}

# ── zip ─────────────────────────────────────────────────────────────────
if (Test-Path $destino) { Remove-Item $destino -Force }
Compress-Archive -Path (Join-Path $tmp "*") -DestinationPath $destino -Force
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

$tam = [math]::Round((Get-Item $destino).Length / 1KB, 1)
Write-Host ""
Write-Host "PRONTO: $destino  ($tam KB)" -ForegroundColor Green
Write-Host "Sem .env, sem perfil de navegador, com os tokens redigidos." -ForegroundColor DarkGray
Write-Host "Anexe esse zip na conversa." -ForegroundColor Cyan
