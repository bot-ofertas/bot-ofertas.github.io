# Reinicia o bot (processo pai + filhos) -- o loop roda uma rodada
# imediatamente ao iniciar, entao isso garante uma rodada fresca logo
# as 10h, já com a priorizacao de itens "MAIS VENDIDO" (ver commit
# 277c751, 2026-08-14) e com as ofertas mais atuais do momento (nao
# uma lista pre-buscada na noite anterior, que ficaria desatualizada).
#
# Mesmo procedimento usado manualmente varias vezes durante a sessao de
# 2026-08-14 -- reaproveitado aqui em vez de escrever logica nova.

$logPath = "D:\bot_ofertas\data\lancamento_10h.log"
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Iniciando lancamento das 10h..." | Out-File -Append -Encoding utf8 $logPath

Get-Process python3.13 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 800

Set-Location D:\bot_ofertas
Start-Process -FilePath "python" -ArgumentList "-u","startup.py" -WorkingDirectory "D:\bot_ofertas" -WindowStyle Hidden

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Bot reiniciado -- rodada imediata deve comecar em segundos." | Out-File -Append -Encoding utf8 $logPath
