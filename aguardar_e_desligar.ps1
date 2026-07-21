# aguardar_e_desligar.ps1
# Chamado pela tarefa agendada BotOfertas-Shutdown às 02:00.
# Se o bot estiver no meio de um ciclo de scraping/postagem, aguarda até
# 35 minutos (checando a cada 60s) antes de desligar — evita matar o PC
# durante um envio ao WhatsApp/Telegram e deixar o banco/clipboard num
# estado inconsistente. Depois de 35 min, desliga de qualquer forma.

$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot
$python = (Get-Command python).Source
$verificar = Join-Path $BASE "verificar_ocioso.py"

$LimiteMin = 35
$IntervaloSeg = 60
$decorridoSeg = 0

while ($decorridoSeg -lt ($LimiteMin * 60)) {
    & $python $verificar | Out-Null
    if ($LASTEXITCODE -eq 0) {
        break
    }
    Start-Sleep -Seconds $IntervaloSeg
    $decorridoSeg += $IntervaloSeg
}

if ($decorridoSeg -ge ($LimiteMin * 60)) {
    shutdown.exe /s /t 60 /c "Desligamento programado — Bot Ofertas (aguardou $LimiteMin min, forçando)"
} else {
    shutdown.exe /s /t 60 /c "Desligamento programado — Bot Ofertas"
}
