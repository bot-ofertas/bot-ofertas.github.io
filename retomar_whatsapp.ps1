# Reativa o WhatsApp apos pausa de 2h (pedido do Daniel, 2026-08-16 17:44 --
# ele precisava usar o PC e a automacao do WhatsApp estava atrapalhando).
# Restaura WHATSAPP_GROUP_ID no .env e reinicia o bot pra pegar a mudanca.

$logPath = "D:\bot_ofertas\data\retomar_whatsapp.log"
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Reativando WhatsApp..." | Out-File -Append -Encoding utf8 $logPath

$envPath = "D:\bot_ofertas\.env"
$conteudo = Get-Content $envPath -Raw
$conteudo = $conteudo.TrimStart([char]0xFEFF)
$conteudo = $conteudo -replace 'WHATSAPP_GROUP_ID_DESATIVADO_TEMPORARIAMENTE=', 'WHATSAPP_GROUP_ID='
# Set-Content -Encoding utf8 no PowerShell 5.1 grava BOM no inicio do arquivo,
# o que quebra o parser de .env do Python (a chave da 1a linha vira "\ufeffTOKEN_TELEGRAM").
# Usar .NET diretamente evita o BOM. (Bug real: derrubou o bot por ~30h em 16-18/08/2026.)
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($envPath, $conteudo, $utf8NoBom)

Get-Process python3.13 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 800

Set-Location D:\bot_ofertas
Start-Process -FilePath "python" -ArgumentList "-u","startup.py" -WorkingDirectory "D:\bot_ofertas" -WindowStyle Hidden

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] WhatsApp reativado, bot reiniciado." | Out-File -Append -Encoding utf8 $logPath
