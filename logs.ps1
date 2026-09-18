# logs.ps1 — Acompanha o log do Bot Ofertas ao vivo
# Uso:  .\logs.ps1              atividade do bot (data\rastreador_local.log)
#       .\logs.ps1 -Erros       relatorio legivel de erros (data\bot.log)
#       .\logs.ps1 -Subida      saida da subida pelo start.ps1
#       .\logs.ps1 -Linhas 100  quantas linhas mostrar antes de seguir
#
# Existe porque o install.ps1 e o start.ps1 mandam rodar `.\logs.ps1` desde
# sempre, e o arquivo nunca existiu: quem seguia a instrucao recebia
# "O termo '.\logs.ps1' nao e reconhecido".
[CmdletBinding()]
param(
    [switch]$Erros,
    [switch]$Subida,
    [int]$Linhas = 40
)

$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot

if ($Erros)       { $arquivo = Join-Path $BASE "data\bot.log";             $rotulo = "erros (core/error_logger.py)" }
elseif ($Subida)  { $arquivo = Join-Path $BASE "data\startup_full.log";    $rotulo = "subida (start.ps1)" }
else              { $arquivo = Join-Path $BASE "data\rastreador_local.log"; $rotulo = "atividade do bot" }

if (-not (Test-Path $arquivo)) {
    Write-Host "Log ainda nao existe: $arquivo" -ForegroundColor Yellow
    Write-Host "O bot ja rodou? Confira com .\status.ps1" -ForegroundColor DarkGray
    # Na primeira subida o arquivo aparece segundos depois; sem este aviso a
    # tela ficava vazia sem explicar nada.
    exit 1
}

Write-Host "== $rotulo ==" -ForegroundColor Cyan
Write-Host "$arquivo  (Ctrl+C encerra)" -ForegroundColor DarkGray
Write-Host ""

# -Wait segue o arquivo enquanto ele cresce, como `tail -f`.
Get-Content $arquivo -Tail $Linhas -Wait
