# aguardar_e_desligar.ps1
# Chamado pela tarefa agendada BotOfertas-Shutdown às 02:00.
# Se o bot estiver no meio de um ciclo de scraping/postagem, aguarda até
# 35 minutos (checando a cada 60s) antes de desligar — evita matar o PC
# durante um envio ao WhatsApp/Telegram e deixar o banco/clipboard num
# estado inconsistente. Depois de 35 min, desliga de qualquer forma.
#
# USA SUSPENSÃO (S3), NÃO DESLIGAMENTO COMPLETO (S5).
# Confirmado em 2026-07-31: com shutdown.exe /s, nem o desligamento nem o
# despertar rodaram nos horários programados (LastRunTime do Agendador
# ficou parado, o PC só voltou quando alguém ligou manualmente à noite).
# Causa: Wake Timer do Windows não acorda de um S5 sem "Wake on RTC"
# habilitado na BIOS (acesso fisico, fora do alcance remoto). Hibernação
# resolveria com o mesmo consumo de energia do desligar completo, mas
# habilitar exige um prompt elevado (admin) que esta automação não tem.
# Suspensão (S3) já está disponível nesta máquina e o Wake Timer acorda
# dela de forma confiável, sem precisar de BIOS nem de admin — troca é
# consumo baixíssimo (poucos watts) durante a madrugada em vez de zero.

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

Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false) | Out-Null
