# aguardar_e_desligar.ps1
# Chamado pela tarefa agendada BotOfertas-Shutdown no fim da janela de
# operação (HORA_DESLIGAR, 02:00 por padrão — ver core/janela.py).
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
$logFile = Join-Path $BASE "data\shutdown.log"
New-Item -ItemType Directory -Force -Path (Join-Path $BASE "data") | Out-Null

function Registrar($linha) {
    Add-Content -Path $logFile -Value ("{0} - {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $linha)
}

# Com $ErrorActionPreference = "Stop", `(Get-Command python).Source` derrubava
# o script inteiro quando o python nao estava no PATH — sem suspender e SEM
# UMA LINHA no log. De manha o sintoma era "o PC amanheceu ligado" e nada
# para explicar. Aqui a falta do python vira registro e a suspensao acontece
# do mesmo jeito: fechar a janela do dia e o trabalho desta tarefa, e uma
# publicacao cortada no meio o bot sabe retomar (fila do WhatsApp + contador
# de falhas da Regra 12); um PC acordado a noite toda, ninguem percebe.
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
$verificar = Join-Path $BASE "verificar_ocioso.py"

if (-not $python) {
    Registrar "AVISO: python nao encontrado no PATH — suspendendo sem checar se o bot esta ocupado"
}
else {
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
        Registrar "Bot seguia ocupado depois de $LimiteMin min — suspendendo assim mesmo"
    }
}

# Sem try/catch aqui, uma falha ao carregar o assembly (perfil sem GUI, .NET
# incompleto) tambem matava o script em silencio, no mesmo ponto cego.
try {
    Add-Type -AssemblyName System.Windows.Forms
}
catch {
    Registrar "ERRO: nao consegui carregar System.Windows.Forms — PC NAO foi suspenso: $($_.Exception.Message)"
    exit 1
}
# force=$true: com $false, qualquer processo que vete o pedido de suspensão
# (Windows Update pendente, driver, processo travado) faz a chamada retornar
# sem suspender e SEM ERRO — o PC fica ligado a noite toda sem log nenhum
# explicando por quê. Rodando às 2h sem ninguém pra clicar em "ok mesmo assim"
# num prompt, force=$true é necessário. O terceiro parâmetro ($false =
# disableWakeEvent) continua False — mantém os Wake Timers habilitados
# (RTCWAKE=1 em agendar_shutdown.ps1), não mude esse.
# Registra a hora de volta ANTES de suspender: depois da chamada o processo
# congela junto com a máquina, e uma linha escrita "depois" pode nunca sair.
# Com a hora prevista no log, quem abre o arquivo de manhã compara com o
# "Wake OK" seguinte e vê na hora se o despertar atrasou ou nem aconteceu.
$volta = "?"
if ($python) {
    $volta = try {
        (& $python -m core.janela --agenda 2>$null | ConvertFrom-Json).ligar
    } catch { "?" }
    if (-not $volta) { $volta = "?" }
}
Registrar "Suspendendo (volta prevista: $volta)"

$ok = [System.Windows.Forms.Application]::SetSuspendState('Suspend', $true, $false)
if ($ok) {
    Registrar "Suspensao OK"
} else {
    Registrar "SetSuspendState retornou FALSE (falhou)"
}
