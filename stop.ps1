# stop.ps1 — Para o Bot Ofertas
$BASE = $PSScriptRoot

# Os QUATRO filhos que o startup.py sobe, mais o proprio pai.
#
# Bug real (achado em 20/09/2026): a lista antiga tinha só "*rastreador.py*",
# que NAO casa com "rastreador_amazon.py" — nem com campanha_ferramentas.py
# ou whatsapp_queue_sender.py. Tres dos quatro filhos sobreviviam a cada
# "Bot parado.", orfaos do pai que acabara de morrer.
#
# O estrago nao e so um processo a mais: orfao vivo continua abrindo rodada
# no banco, entao `execucao_em_andamento()` segue dizendo SIM e o passo 2 do
# aplicar_tudo.ps1 recusa reiniciar — para sempre. Foi exatamente o circulo
# em que o Daniel ficou preso: o script dizia "espere a rodada terminar" de
# uma rodada que era de um processo que o proprio script deveria ter matado.
$PADROES_BOT = @(
    "*startup.py*",                 # o processo PAI (Regra 10)
    "*rastreador.py*",              # ML
    "*rastreador_amazon.py*",       # Amazon
    "*campanha_ferramentas.py*",    # campanha de ferramentas
    "*whatsapp_queue_sender.py*",   # consumidor da fila de WhatsApp
    "*setup_whatsapp*"
)

$ps = Get-CimInstance Win32_Process | Where-Object {
    $linha = $_.CommandLine
    ($linha -and ($PADROES_BOT | Where-Object { $linha -like $_ })) -or
    ($_.Name -eq "chrome.exe" -and $linha -like "*chrome_bot*")
}

if (-not $ps) {
    Write-Host "Bot já está parado." -ForegroundColor Yellow
    exit 0
}

foreach ($p in $ps) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "  encerrado PID $($p.ProcessId) [$($p.Name)]" -ForegroundColor DarkGray
}
Start-Sleep -Seconds 2

# Conferir em vez de anunciar. "Bot parado." impresso sem olhar foi o que
# deixou os tres orfaos invisiveis: a mensagem dizia uma coisa e o Gerenciador
# de Tarefas mostrava outra.
$sobrou = Get-CimInstance Win32_Process | Where-Object {
    $linha = $_.CommandLine
    $linha -and ($PADROES_BOT | Where-Object { $linha -like $_ })
}
if ($sobrou) {
    Write-Host "AVISO: ainda ha processo do bot vivo:" -ForegroundColor Yellow
    foreach ($p in $sobrou) {
        Write-Host "  PID $($p.ProcessId)  $($p.CommandLine)" -ForegroundColor DarkGray
    }
    Write-Host "  Rode de novo, ou encerre pelo Gerenciador de Tarefas." -ForegroundColor DarkGray
    exit 1
}
Write-Host "Bot parado." -ForegroundColor Green
