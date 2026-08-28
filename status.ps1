# status.ps1 — Estado do Bot Ofertas
$BASE = $PSScriptRoot

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  BOT OFERTAS — STATUS" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ── Processos ────────────────────────────────────────────────────────────────
# Uma única consulta ao Win32_Process para todos os filtros. A versão
# anterior chamava Get-CimInstance 3x (uma por processo procurado) — em
# máquina carregada isso deixava o status visivelmente lento, e ainda assim
# só mostrava 1 dos 4 processos que o startup.py sobe.
# Get-CimInstance só existe no Windows. `-ErrorAction SilentlyContinue` NÃO
# silencia "termo não reconhecido" (isso é erro de resolução de comando, não
# do cmdlet), então rodar este script fora do Windows — pelo VS Code no WSL,
# por exemplo — despejava dois blocos vermelhos antes de mostrar qualquer
# coisa. $IsWindows não existe no Windows PowerShell 5.1: ali ele é $null, e
# nesse caso estamos necessariamente no Windows.
$ehWindows = ($null -eq $IsWindows) -or $IsWindows

$procs = @()
if ($ehWindows) {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue)
}

function Get-BotProc($padrao) {
    return @($procs | Where-Object { $_.CommandLine -like $padrao })
}

$monitorados = [ordered]@{
    "Rastreador ML"       = "*rastreador.py*"
    "Rastreador Amazon"   = "*rastreador_amazon.py*"
    "Campanha Ferramentas" = "*campanha_ferramentas.py*"
    "Fila WhatsApp"       = "*whatsapp_queue_sender.py*"
    "Startup (pai)"       = "*startup.py*"
}

Write-Host "`nProcessos:" -ForegroundColor Yellow
foreach ($nome in $monitorados.Keys) {
    $achados = Get-BotProc $monitorados[$nome]
    $rotulo = $nome.PadRight(21)
    if ($achados.Count -gt 0) {
        $pids = ($achados | ForEach-Object { $_.ProcessId }) -join ", "
        Write-Host "  $rotulo RODANDO (PID $pids)" -ForegroundColor Green
        if ($achados.Count -gt 1 -and $nome -ne "Startup (pai)") {
            # Instância duplicada = post repetido no canal. Vale gritar.
            Write-Host "  $(' ' * 21) ATENCAO: $($achados.Count) instancias!" -ForegroundColor Red
        }
    }
    elseif ($nome -eq "Startup (pai)") {
        Write-Host "  $rotulo PARADO" -ForegroundColor DarkGray
    }
    else {
        Write-Host "  $rotulo PARADO" -ForegroundColor Red
    }
}

$wa = @()
if ($ehWindows) {
    $wa = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -in "WhatsApp.exe", "WhatsApp.Root.exe" })
}
if ($wa) { Write-Host "  WhatsApp Desktop      RODANDO" -ForegroundColor Green }
elseif (-not $ehWindows) { Write-Host "  WhatsApp Desktop      (nao verificavel fora do Windows)" -ForegroundColor DarkGray }
else { Write-Host "  WhatsApp Desktop      NAO ENCONTRADO" -ForegroundColor Red }

# ── Healthcheck ──────────────────────────────────────────────────────────────
Write-Host "`nHealthcheck (http://127.0.0.1:8724/health):" -ForegroundColor Yellow

function Get-Health($url) {
    # /health responde 503 (de propósito) quando um componente crítico está
    # fora — e Invoke-RestMethod trata QUALQUER status >= 400 como exceção.
    # Por isso o script antigo caía no catch e imprimia "bot não está
    # rodando" justamente quando o bot ESTAVA rodando e tinha algo a
    # relatar: o único caso em que esta tela importa. Aqui o corpo da
    # resposta de erro é lido e devolvido normalmente.
    try {
        return Invoke-RestMethod -Uri $url -TimeoutSec 3
    }
    catch {
        $resp = $_.Exception.Response
        if ($null -ne $resp) {
            # PowerShell 7+ já traz o corpo em ErrorDetails
            if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
                try { return $_.ErrorDetails.Message | ConvertFrom-Json } catch { }
            }
            # Windows PowerShell 5.1: lê o stream da resposta
            try {
                $stream = $resp.GetResponseStream()
                $leitor = New-Object System.IO.StreamReader($stream)
                $texto = $leitor.ReadToEnd()
                $leitor.Close()
                if ($texto) { return $texto | ConvertFrom-Json }
            }
            catch { }
        }
        return $null
    }
}

$h = Get-Health "http://127.0.0.1:8724/health"
if ($null -ne $h) {
    $waStatus = if ($h.whatsapp.ok) { "OK ($($h.whatsapp.metodo))" } else { "OFF ($($h.whatsapp.motivo))" }
    Write-Host "  Telegram:   $($h.telegram.ok)" -ForegroundColor $(if ($h.telegram.ok) { "Green" } else { "Red" })
    Write-Host "  WhatsApp:   $waStatus" -ForegroundColor $(if ($h.whatsapp.ok) { "Green" } else { "Red" })
    Write-Host "  Rastreador: $($h.rastreador.ok)" -ForegroundColor $(if ($h.rastreador.ok) { "Green" } else { "Red" })
    $cpu = if ($null -ne $h.sistema.cpu) { "$($h.sistema.cpu)%" } else { "n/d" }
    $ram = if ($null -ne $h.sistema.ram_pct) { "$($h.sistema.ram_pct)%" } else { "n/d" }
    Write-Host "  CPU: $cpu | RAM: $ram"
    if ($cpu -eq "n/d") {
        # O bloco `sistema` vem vazio quando o psutil não importa dentro da
        # thread do healthcheck — vale dizer isso em vez de mostrar "%".
        Write-Host "  (sem CPU/RAM: psutil indisponivel no processo do bot)" -ForegroundColor DarkGray
    }

    if ($h.criticos_com_falha -and $h.criticos_com_falha.Count -gt 0) {
        Write-Host "  Componentes criticos com falha: $($h.criticos_com_falha -join ', ')" -ForegroundColor Red
    }

    # n8n
    if ($null -ne $h.n8n) {
        if ($h.n8n.ativo) {
            $pend = $h.n8n.spool_pendente
            $cor = if ($pend -gt 0) { "Yellow" } else { "Green" }
            Write-Host "  n8n:        ATIVO ($($h.n8n.url))" -ForegroundColor Green
            Write-Host "              eventos na fila local: $pend" -ForegroundColor $cor
            if ($h.n8n.ultimo_envio.ok -eq $false) {
                Write-Host "              ultimo envio FALHOU: $($h.n8n.ultimo_envio.erro)" -ForegroundColor Red
            }
        }
        else {
            Write-Host "  n8n:        desativado (sem N8N_WEBHOOK_URL)" -ForegroundColor DarkGray
        }
    }

    # Pausa e quarentena
    if ($h.pausa.pausado) {
        Write-Host "  PUBLICACAO PAUSADA desde $($h.pausa.pausado_em) — $($h.pausa.motivo)" -ForegroundColor Yellow
        Write-Host "  (retomar: Remove-Item '$BASE\data\pausado.flag')" -ForegroundColor DarkGray
    }
    if ($h.quarentena.total -gt 0) {
        Write-Host "  Quarentena: $($h.quarentena.total) produto(s) fora de rotacao" -ForegroundColor Yellow
        foreach ($pid_prod in $h.quarentena.produtos) {
            Write-Host "              - $pid_prod" -ForegroundColor DarkGray
        }
    }
}
else {
    Write-Host "  Healthcheck OFF (nao respondeu na porta 8724)" -ForegroundColor Red
}

# ── Últimos erros ────────────────────────────────────────────────────────────
Write-Host "`nUltimos erros (data/errors.jsonl):" -ForegroundColor Yellow
$errFile = Join-Path $BASE "data\errors.jsonl"
if (Test-Path $errFile) {
    $tail = Get-Content $errFile -Tail 3
    if ($tail) {
        foreach ($line in $tail) {
            # Uma linha truncada (escrita pela metade no momento da leitura)
            # fazia ConvertFrom-Json estourar e derrubar o script inteiro
            # antes de mostrar a última seção.
            try {
                $e = $line | ConvertFrom-Json
                Write-Host "  [$($e.ts)] $($e.operacao): $($e.mensagem)" -ForegroundColor DarkGray
            }
            catch {
                Write-Host "  (linha ilegivel no errors.jsonl)" -ForegroundColor DarkGray
            }
        }
    }
    else { Write-Host "  (nenhum)" -ForegroundColor Green }
}
else { Write-Host "  (nenhum)" -ForegroundColor Green }

# ── Última rodada ────────────────────────────────────────────────────────────
Write-Host "`nUltima atividade (data/rastreador_local.log):" -ForegroundColor Yellow
$log = Join-Path $BASE "data\rastreador_local.log"
if (Test-Path $log) {
    $last = Get-Content $log -Tail 40 |
            Where-Object { $_ -match "Publicado|WhatsApp|Rodada|quarentena" } |
            Select-Object -Last 3
    if ($last) { $last | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray } }
    else { Write-Host "  (sem eventos recentes)" -ForegroundColor DarkGray }
}
else { Write-Host "  (log ainda nao criado)" -ForegroundColor DarkGray }
Write-Host ""
