# diagnostico_whatsapp.ps1 - responde POR QUE o WhatsApp nao esta postando.
#
# O envio tem quatro tentativas em ordem (Evolution API -> Playwright ->
# WhatsApp Desktop -> pyautogui) e cada falha grava uma etiqueta propria em
# data\errors.jsonl. Adivinhar qual delas e a sua custa rodadas; ler a
# etiqueta responde na hora. Este script so LE - nao muda nada, nao reinicia
# nada, nao toca no .env.
#
# Uso:  .\diagnostico_whatsapp.ps1

$BASE = $PSScriptRoot
if (-not $BASE) { $BASE = (Get-Location).Path }

function Titulo($t) { Write-Host ""; Write-Host "== $t ==" -ForegroundColor Cyan }
function Ok($t)     { Write-Host "  OK   $t" -ForegroundColor Green }
function Falta($t)  { Write-Host "  FALTA $t" -ForegroundColor Red }
function Nota($t)   { Write-Host "       $t" -ForegroundColor DarkGray }

Titulo "1. Configuracao do grupo (.env - so os nomes, nunca o valor)"
$envPath = Join-Path $BASE ".env"
$grupoNome = $null
if (Test-Path $envPath) {
    foreach ($chave in 'WHATSAPP_GROUP_ID','WHATSAPP_GROUP_NAME','EVOLUTION_API_URL','EVOLUTION_API_KEY','WHATSAPP_CHROME_FALLBACK') {
        $linha = (Get-Content $envPath | Where-Object { $_ -match "^\s*$chave\s*=" } | Select-Object -First 1)
        if (-not $linha) { Falta "$chave ausente" ; continue }
        $valor = ($linha -split '=',2)[1].Trim()
        if ([string]::IsNullOrWhiteSpace($valor)) { Falta "$chave vazio" }
        else {
            Ok "$chave definido ($($valor.Length) caracteres)"
            if ($chave -eq 'WHATSAPP_GROUP_NAME') { $grupoNome = $valor }
        }
    }
}
else { Falta ".env nao encontrado em $BASE" }
if (-not $grupoNome) {
    $grupoNome = "Bot-Ofertas"
    Nota "sem WHATSAPP_GROUP_NAME a automacao procura a conversa chamada exatamente 'Bot-Ofertas'"
}
Write-Host "  -> a busca (Ctrl+F) vai procurar por: '$grupoNome'" -ForegroundColor Yellow

Titulo "2. WhatsApp Desktop esta rodando?"
$wa = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'WhatsApp' }
if ($wa) {
    foreach ($p in $wa) { Ok "$($p.ProcessName) (PID $($p.Id))" }
    $comJanela = $wa | Where-Object { $_.MainWindowTitle }
    if ($comJanela) { foreach ($p in $comJanela) { Nota "janela: '$($p.MainWindowTitle)'" } }
    else { Nota "processo de pe, mas SEM janela enumeravel (minimizado na bandeja)" }
}
else { Falta "WhatsApp Desktop NAO esta rodando - sem ele nao ha como enviar" }

Titulo "3. Quem drena a fila esta de pe?"
$pidFile = Join-Path $BASE "data\whatsapp_queue_sender.pid"
if (Test-Path $pidFile) {
    $wpid = (Get-Content $pidFile -Raw).Trim()
    $proc = Get-Process -Id $wpid -ErrorAction SilentlyContinue
    if ($proc) { Ok "whatsapp_queue_sender vivo (PID $wpid)" }
    else { Falta "PID $wpid registrado mas o processo NAO existe - a fila nao esta sendo drenada" }
}
else { Falta "data\whatsapp_queue_sender.pid ausente - o drenador nunca subiu nesta versao do startup.py" }

Titulo "4. Tamanho da fila"
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($py) {
    & $py -c "import sqlite3,os; d=os.path.join('data','bot_ofertas.db'); c=sqlite3.connect(d); n=c.execute('SELECT COUNT(*) FROM fila_whatsapp WHERE enviado_em IS NULL').fetchone()[0]; e=c.execute('SELECT COUNT(*) FROM fila_whatsapp WHERE enviado_em IS NOT NULL').fetchone()[0]; print('  pendentes:',n,' | ja enviados:',e)" 2>&1
}
else { Nota "python nao encontrado no PATH" }

Titulo "5. Ultimas falhas registradas (data\errors.jsonl)"
$erros = Join-Path $BASE "data\errors.jsonl"
if (Test-Path $erros) {
    $linhas = Get-Content $erros -Tail 400
    $wa_err = @()
    foreach ($l in $linhas) {
        try { $o = $l | ConvertFrom-Json } catch { continue }
        if ($o.operacao -match '^wa_') { $wa_err += $o }
    }
    if ($wa_err.Count -eq 0) { Nota "nenhuma falha de WhatsApp nas ultimas 400 linhas" }
    else {
        $wa_err | Group-Object operacao | Sort-Object Count -Descending | ForEach-Object {
            Write-Host ("  {0,3}x  {1}" -f $_.Count, $_.Name) -ForegroundColor Yellow
        }
        Write-Host ""
        Nota "ultima ocorrencia de cada:"
        $wa_err | Group-Object operacao | ForEach-Object {
            $u = $_.Group[-1]
            Write-Host ("   [{0}] {1}" -f $u.operacao, $u.mensagem) -ForegroundColor DarkYellow
        }
    }
}
else { Falta "data\errors.jsonl nao existe" }

Titulo "6. Log do drenador (ultimas 25 linhas)"
$wlog = Join-Path $BASE "data\whatsapp_queue_sender.log"
if (Test-Path $wlog) {
    Nota ("modificado em " + (Get-Item $wlog).LastWriteTime)
    Get-Content $wlog -Tail 25
}
else { Falta "data\whatsapp_queue_sender.log nao existe" }

Titulo "7. Linhas de WhatsApp no log principal"
$blog = Join-Path $BASE "data\bot.log"
if (Test-Path $blog) {
    Get-Content $blog -Tail 600 | Select-String -Pattern 'WhatsApp|wa_|fila' | Select-Object -Last 20
}
else { Falta "data\bot.log nao existe" }

Write-Host ""
Write-Host "Copie tudo acima e cole na conversa." -ForegroundColor Cyan
