# install.ps1 — Instalador completo do Bot Ofertas via PowerShell
# Uso:  .\install.ps1                 instalacao completa
#       .\install.ps1 -PularDocker    nao mexe na Evolution API
#
# Pensado para rodar numa maquina LIMPA, do zero: Python, dependencias,
# navegador do Playwright, .env, Docker da Evolution API, tarefa de
# auto-start e, no fim, a verificacao que responde "a instalacao ficou de
# pe?" (core/healthcheck.py).
#
# Etapa obrigatoria que falha para o script na hora, dizendo o motivo.
# Etapa opcional avisa e segue: o bot publica no Telegram sem Docker e sem
# Playwright (Regra 6 — o Telegram nunca depende do resto).

[CmdletBinding()]
param(
    # A Evolution API resolve o WhatsApp onde nao existe WhatsApp Desktop
    # (servidor Linux, Regra 16). No PC, quem envia e a automacao da janela
    # do app (Regra 5) e o Docker Desktop nao e necessario.
    [switch]$PularDocker
)

$ErrorActionPreference = "Stop"
$BASE = $PSScriptRoot
Set-Location $BASE

# setup_whatsapp_api.py anota `dict | None` em assinatura de funcao, que e
# avaliada na definicao: em Python 3.9 o arquivo nem importa. O container de
# producao roda 3.13 e o CI roda 3.11.
$PY_MINIMO = [version]"3.10"

$avisos = New-Object System.Collections.Generic.List[string]

function Etapa($numero, $titulo) {
    Write-Host "`n[$numero] $titulo" -ForegroundColor Yellow
}
function Ok($mensagem)    { Write-Host "  OK: $mensagem" -ForegroundColor Green }
function Aviso($mensagem) {
    Write-Host "  AVISO: $mensagem" -ForegroundColor Yellow
    $avisos.Add($mensagem)
}
function Parar($mensagem) {
    Write-Host "  ERRO: $mensagem" -ForegroundColor Red
    Write-Host ""
    exit 1
}

function Invoke-Nativo {
    <#
        Roda um executavel e devolve saida + codigo de saida.

        Existe por um detalhe que estragava a etapa mais importante deste
        script: com $ErrorActionPreference = "Stop", o Windows PowerShell 5.1
        transforma QUALQUER escrita em stderr de um comando nativo num erro
        TERMINANTE (NativeCommandError). O atalho `python.exe` da Microsoft
        Store escreve justamente em stderr — ou seja, a checagem de Python
        morria com uma excecao de PowerShell em vez de explicar o problema.
        Aqui o preference cai para "Continue" so durante a chamada, e quem
        decide o resultado e o codigo de saida.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Argumentos = @()
    )
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $saida = & $Exe @Argumentos 2>&1 | Out-String
        return [pscustomobject]@{ Saida = $saida.TrimEnd(); Codigo = $LASTEXITCODE }
    }
    catch {
        return [pscustomobject]@{ Saida = $_.Exception.Message; Codigo = 1 }
    }
    finally { $ErrorActionPreference = $anterior }
}

function Get-UltimasLinhas($texto, $quantas = 4) {
    if (-not $texto) { return "(sem saida)" }
    return (($texto -split "`r?`n" | Where-Object { $_.Trim() } |
             Select-Object -Last $quantas) -join " | ")
}

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  BOT OFERTAS - INSTALACAO COMPLETA" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ─── 1. Python ────────────────────────────────────────────────────────────
Etapa "1/8" "Verificando Python (minimo $PY_MINIMO)..."
$cmdPython = Get-Command python -ErrorAction SilentlyContinue
if (-not $cmdPython) {
    Parar ("Python nao encontrado no PATH. Instale o Python $PY_MINIMO ou mais " +
           "novo de https://python.org e marque 'Add python.exe to PATH'.")
}

$probe = Invoke-Nativo "python" @("--version")
if ($probe.Codigo -ne 0 -or $probe.Saida -notmatch 'Python\s+(\d+)\.(\d+)') {
    # O Windows 10/11 ja vem com um ATALHO chamado python.exe em
    # WindowsApps que nao e Python: ele abre a Microsoft Store e sai com
    # 9009. Get-Command acha esse atalho, entao "existe python" nao e
    # "existe Python" — e o instalador antigo dava OK aqui e so quebrava
    # la no pip, sem dizer por que.
    if ($cmdPython.Source -like "*WindowsApps*") {
        Parar ("o 'python' do PATH e o atalho da Microsoft Store, nao o " +
               "Python. Instale de https://python.org (marque 'Add python.exe " +
               "to PATH') ou desligue o atalho em Configuracoes > Aplicativos " +
               "> Aliases de execucao de aplicativo.")
    }
    Parar "nao consegui ler a versao do Python: $(Get-UltimasLinhas $probe.Saida 2)"
}

$versaoPython = [version]"$($Matches[1]).$($Matches[2])"
if ($versaoPython -lt $PY_MINIMO) {
    Parar ("Python $versaoPython e antigo demais para este projeto " +
           "(minimo $PY_MINIMO). Atualize em https://python.org.")
}
Ok "Python $versaoPython em $($cmdPython.Source)"

# ─── 2. Dependencias Python ───────────────────────────────────────────────
Etapa "2/8" "Instalando dependencias (requirements.txt)..."
$r = Invoke-Nativo "python" @("-m", "pip", "install", "--upgrade", "pip",
                              "--disable-pip-version-check", "--quiet")
if ($r.Codigo -ne 0) { Aviso "nao consegui atualizar o pip; seguindo com o que ja existe" }

$r = Invoke-Nativo "python" @("-m", "pip", "install", "-r", "requirements.txt",
                              "--disable-pip-version-check", "--quiet")
if ($r.Codigo -ne 0) {
    # A saida do pip era engolida por `--quiet` + `Out-Null`: a instalacao
    # parava com "ERRO no pip install" e nada sobre QUAL pacote falhou.
    Write-Host $r.Saida -ForegroundColor DarkGray
    Parar "pip install -r requirements.txt falhou (saida acima). Sem os pacotes o bot nao sobe."
}
Ok "dependencias instaladas"

# ─── 3. Chromium do Playwright (opcional) ─────────────────────────────────
Etapa "3/8" "Instalando o Chromium do Playwright (raspagem do ML)..."
$r = Invoke-Nativo "python" @("-m", "playwright", "install", "chromium")
if ($r.Codigo -ne 0) {
    # Antes: `2>&1 | Out-Null` e um "OK: Chromium instalado" fixo logo
    # depois — dizia sucesso mesmo quando o download falhava.
    Aviso ("Chromium do Playwright nao instalou. A raspagem que depende de " +
           "navegador fica fora; o resto do bot funciona. " +
           "Saida: $(Get-UltimasLinhas $r.Saida)")
}
else { Ok "Chromium instalado" }

# ─── 4. .env ──────────────────────────────────────────────────────────────
Etapa "4/8" "Preparando o .env..."
$envPath = Join-Path $BASE ".env"
$envExemplo = Join-Path $BASE ".env.example"
if (Test-Path $envPath) {
    Ok ".env ja existe (mantido como esta)"
}
elseif (Test-Path $envExemplo) {
    Copy-Item $envExemplo $envPath
    Ok ".env criado a partir do .env.example"
    Aviso ("o .env esta com os valores de EXEMPLO. Preencha TOKEN_TELEGRAM e " +
           "CANAL_GERAL — a etapa 8/8 cobra isso.")
}
else {
    Parar ".env.example nao existe; nao tenho de onde criar o .env"
}

# ─── 5. Pasta data\ ───────────────────────────────────────────────────────
Etapa "5/8" "Criando a pasta data\..."
New-Item -ItemType Directory -Force -Path (Join-Path $BASE "data") | Out-Null
Ok (Join-Path $BASE "data")

# ─── 6. Evolution API em Docker (opcional) ────────────────────────────────
Etapa "6/8" "Evolution API em Docker (WhatsApp por API)..."
$ymlEvolution = Join-Path $BASE "docker\evolution.yml"
if ($PularDocker) {
    Ok "pulado por -PularDocker"
}
elseif (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Aviso ("Docker nao encontrado. No PC isso e normal: o envio do WhatsApp " +
           "usa o app Desktop (Regra 5). Para usar a Evolution API, instale o " +
           "Docker Desktop e rode .\install.ps1 de novo.")
}
elseif ((Invoke-Nativo "docker" @("info")).Codigo -ne 0) {
    Aviso ("Docker esta instalado mas o daemon nao responde — abra o Docker " +
           "Desktop e espere ficar 'running', depois rode .\install.ps1 de novo.")
}
elseif (-not (Test-Path $ymlEvolution)) {
    Aviso "docker\evolution.yml nao existe; nada a subir"
}
else {
    # A chave nao tem padrao no compose de proposito (repo publico): sem ela
    # o `docker compose` recusa subir. Gerar uma local aqui e o que o
    # .env.example manda fazer a mao.
    $envTexto = [System.IO.File]::ReadAllText($envPath)
    if ($envTexto -notmatch '(?m)^EVOLUTION_API_KEY=\S') {
        $nova = (Invoke-Nativo "python" @("-c", "import secrets; print(secrets.token_hex(16))")).Saida.Trim()
        if ($nova -match '^[0-9a-f]{32}$') {
            if ($envTexto -match '(?m)^EVOLUTION_API_KEY=.*$') {
                $envTexto = [regex]::Replace($envTexto, '(?m)^EVOLUTION_API_KEY=.*$',
                                             "EVOLUTION_API_KEY=$nova")
            }
            else {
                $envTexto = $envTexto.TrimEnd() + "`nEVOLUTION_API_KEY=$nova`n"
            }
            # Set-Content -Encoding UTF8 do PowerShell 5.1 grava BOM, e o
            # python-dotenv leria a PRIMEIRA variavel do arquivo como
            # "\ufeffTOKEN_TELEGRAM": a chave estaria la e o bot nao a
            # acharia. WriteAllText com UTF8Encoding($false) nao poe BOM.
            [System.IO.File]::WriteAllText($envPath, $envTexto,
                                           (New-Object System.Text.UTF8Encoding($false)))
            Ok "EVOLUTION_API_KEY gerada e gravada no .env (fica so nesta maquina)"
        }
        else {
            Aviso "nao consegui gerar a EVOLUTION_API_KEY; preencha no .env a mao"
        }
    }

    # `--env-file` NAO e enfeite. Sem ele o docker compose procura o .env ao
    # lado do YML (docker\.env), nao na raiz do projeto, e recusa subir com
    # "required variable EVOLUTION_API_KEY is missing a value" mesmo com a
    # chave preenchida na raiz. Era exatamente o comando que este script
    # imprimia no fim como instrucao — e que nunca funcionava.
    $r = Invoke-Nativo "docker" @("compose", "--env-file", $envPath,
                                  "-f", $ymlEvolution, "up", "-d")
    if ($r.Codigo -ne 0) {
        Aviso "docker compose up falhou: $(Get-UltimasLinhas $r.Saida)"
    }
    else {
        $noAr = $false
        foreach ($tentativa in 1..20) {
            Start-Sleep -Seconds 3
            try {
                $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8080/" -TimeoutSec 3 -UseBasicParsing
                if ($resp.StatusCode -lt 500) { $noAr = $true; break }
            }
            catch {
                # Sem apikey a Evolution responde 401/404 — que e resposta,
                # nao queda. Invoke-WebRequest trata >= 400 como excecao.
                $codigo = 0
                if ($_.Exception.Response) { $codigo = [int]$_.Exception.Response.StatusCode }
                if ($codigo -ge 100 -and $codigo -lt 500) { $noAr = $true; break }
            }
        }
        if ($noAr) {
            Ok "Evolution API no ar em http://127.0.0.1:8080 (loopback, Regra 16)"
            Write-Host ("     Falta ler o QR no celular UMA vez: " +
                        "python setup_whatsapp_api.py") -ForegroundColor Cyan
        }
        else {
            Aviso ("container subiu mas a API nao respondeu na porta 8080 em 60s. " +
                   "Logs: docker compose --env-file .env -f docker\evolution.yml logs")
        }
    }
}

# ─── 7. Tarefa de auto-start no login ─────────────────────────────────────
Etapa "7/8" "Registrando a tarefa de auto-start no login..."
$nomeTarefa = "BotOfertas-AutoStart"
try {
    $usuario = "$env:USERDOMAIN\$env:USERNAME"
    $acao = New-ScheduledTaskAction -Execute $cmdPython.Source `
        -Argument "-u `"$(Join-Path $BASE 'startup.py')`"" -WorkingDirectory $BASE
    $gatilho = New-ScheduledTaskTrigger -AtLogOn -User $usuario
    # Sem -StartWhenAvailable: aqui nao faz estrago (esta tarefa nao desliga
    # nada), mas a Regra 15 proibe nas tarefas do ciclo e manter o padrao
    # evita copiar a linha errada para uma tarefa que desliga o PC.
    $config = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) `
        -MultipleInstances IgnoreNew -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive

    Register-ScheduledTask -TaskName $nomeTarefa -Action $acao -Trigger $gatilho `
        -Settings $config -Principal $principal `
        -Description "Bot Ofertas - Telegram + WhatsApp automatico" `
        -Force | Out-Null
    Ok "tarefa '$nomeTarefa' registrada ($usuario)"
}
catch {
    # Antes isto derrubava a instalacao INTEIRA na ultima etapa, jogando fora
    # tudo o que ja tinha dado certo. A tarefa e conveniencia: o bot inicia
    # igual com .\start.ps1.
    Aviso ("nao consegui registrar a tarefa '$nomeTarefa': $($_.Exception.Message) " +
           "O bot inicia normalmente com .\start.ps1.")
}

# ─── 8. Verificacao final ─────────────────────────────────────────────────
Etapa "8/8" "Verificacao final (core/healthcheck.py)..."
$verificacao = Invoke-Nativo "python" @("-m", "core.healthcheck")
Write-Host $verificacao.Saida
$instalacaoOk = ($verificacao.Codigo -eq 0)

# ─── Conclusao ────────────────────────────────────────────────────────────
Write-Host "==============================================" -ForegroundColor Cyan
if ($instalacaoOk) {
    Write-Host "  INSTALACAO CONCLUIDA" -ForegroundColor Green
}
else {
    Write-Host "  INSTALACAO INCOMPLETA" -ForegroundColor Yellow
}
Write-Host "==============================================" -ForegroundColor Cyan

if ($avisos.Count -gt 0) {
    Write-Host "`nAvisos ($($avisos.Count)):" -ForegroundColor Yellow
    foreach ($a in $avisos) { Write-Host "  - $a" -ForegroundColor DarkGray }
}

Write-Host "`nOperacao:" -ForegroundColor Yellow
Write-Host "  .\start.ps1     inicia o bot (processo pai startup.py)"
Write-Host "  .\status.ps1    estado dos processos, /health e ultimos erros"
Write-Host "  .\logs.ps1      acompanha o log ao vivo"
Write-Host "  .\stop.ps1      para o bot"
Write-Host ""

if (-not $instalacaoOk) {
    Write-Host ("Resolva os itens [FALHA] da etapa 8/8 e rode " +
                ".\install.ps1 de novo (ou so: python -m core.healthcheck).") -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
