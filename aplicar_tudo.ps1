# aplicar_tudo.ps1 — aplica a branch e devolve o bot ao ar, num comando so.
#
# Existe porque a sequencia manual (stop -> fetch -> checkout -> configurar
# ciclo -> start) tem cinco pontos de falha silenciosa, e cada um deles ja
# aconteceu de verdade neste projeto:
#
#   1. `git checkout` recusa se houver alteracao local nao commitada — e a
#      mensagem do git some no meio do resto, entao os passos seguintes rodam
#      no codigo ANTIGO achando que estao no novo;
#   2. `agendar_shutdown.ps1` registra tarefas do Agendador, o que exige
#      Administrador; sem isso ele falha tarefa por tarefa e o ciclo fica
#      pela metade;
#   3. `(Get-Command python).Source` com $ErrorActionPreference = "Stop"
#      derruba o script inteiro quando o python nao esta no PATH, sem log
#      (foi assim que o PC amanhecia ligado sem ninguem saber por que);
#   4. `start.ps1` chama Popen e declara vitoria — mas com .env invalido o
#      startup.py morre em ~1s, e "chamar Popen nao e o mesmo que ter subido"
#      (Regra 15);
#   5. parar o bot no meio de uma rodada de publicacao (Regra 10).
#
# Uso:
#   .\aplicar_tudo.ps1                → aplica tudo
#   .\aplicar_tudo.ps1 -SemAgenda     → aplica sem mexer no Agendador
#                                       (util quando nao da pra abrir como
#                                       Administrador agora)

[CmdletBinding()]
param(
    [string]$Branch = "claude/bot-ofertas-n8n-8d7qe2",
    [switch]$SemAgenda,
    [switch]$SemDependencias,
    # Saida de emergencia: se a busca automatica nao achar, aponte o caminho
    #   .\aplicar_tudo.ps1 -Git "C:\...\git.exe" -Python "C:\...\python.exe"
    [string]$Git = "",
    [string]$Python = ""
)

$BASE = $PSScriptRoot
Set-Location $BASE

function Passo($n, $texto) { Write-Host "`n[$n] $texto" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  OK: $t"   -ForegroundColor Green }
function Aviso($t){ Write-Host "  AVISO: $t" -ForegroundColor Yellow }
function Erro($t) { Write-Host "  ERRO: $t"  -ForegroundColor Red }



# -------------------------------------------------- achar git e python
# "Existe no PATH" nao e o mesmo que "funciona" — dois motivos reais,
# os dois vistos no PC do Daniel em 08/09/2026 numa janela de Administrador:
#
#   1. O Windows deixa ATALHOS em ...\WindowsApps\python.exe que nao sao o
#      Python: executados, so abrem a Microsoft Store ("Python was not
#      found; run without arguments to install..."). `Get-Command python`
#      encontra esse atalho, entao a checagem "existe?" passava e o script
#      seguia sem Python nenhum.
#   2. Elevar para Administrador pode trocar o perfil de usuario, e com ele
#      o PATH: `git` sumiu por completo ("O termo 'git' nao e reconhecido").
#
# Por isso aqui se TESTA cada candidato de verdade (`--version`) e, se o
# PATH nao servir, se procura nos lugares onde esses programas costumam ser
# instalados.
$problemas = 0

function Testar-Programa($caminho) {
    if (-not $caminho) { return $false }
    try {
        $saida = & $caminho --version 2>&1 | Out-String
        return ($LASTEXITCODE -eq 0 -and $saida -match '\d+\.\d+')
    }
    catch { return $false }
}

# O instalador do Git e o do Python registram onde se instalaram. E a fonte
# mais confiavel: independe do PATH, de quem elevou a janela e de o programa
# ter sido instalado "so para mim" ou "para todos".
function Caminhos-Do-Registro($chaves, $sufixo) {
    $achados = @()
    foreach ($chave in $chaves) {
        foreach ($k in (Get-ChildItem $chave -ErrorAction SilentlyContinue)) {
            $v = (Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue).'(default)'
            if ($v) { $achados += (Join-Path $v $sufixo) }
        }
        $direto = (Get-ItemProperty $chave -ErrorAction SilentlyContinue).InstallPath
        if ($direto) { $achados += (Join-Path $direto $sufixo) }
    }
    return $achados
}

function Achar-Programa($nome, [string[]]$ondeProcurar, [string[]]$doRegistro = @()) {
    $script:ultimaBusca = @()
    $candidatos = @()
    $doPath = Get-Command $nome -ErrorAction SilentlyContinue
    if ($doPath) { $candidatos += $doPath.Source }
    $candidatos += $doRegistro
    foreach ($padrao in $ondeProcurar) {
        $script:ultimaBusca += $padrao
        $candidatos += (Get-ChildItem $padrao -ErrorAction SilentlyContinue |
                        Sort-Object FullName -Descending |
                        ForEach-Object { $_.FullName })
    }
    foreach ($c in $candidatos) { if (Testar-Programa $c) { return $c } }
    return $null
}

# Sem git, os arquivos NOVOS da branch nao existem no disco —
# `configurar_ciclo.ps1` nem sequer existe na main, entao o passo do
# Agendador chamaria um arquivo inexistente. Buscar so o que o ciclo precisa,
# do proprio repositorio do Daniel, resolve sem exigir a instalacao do git.
function Buscar-DaBranch($nome) {
    $destino = Join-Path $BASE $nome
    $url = "https://raw.githubusercontent.com/bot-ofertas/bot-ofertas.github.io/$Branch/$nome"
    try {
        $pasta = Split-Path $destino
        if (-not (Test-Path $pasta)) { New-Item -ItemType Directory -Force -Path $pasta | Out-Null }
        Invoke-WebRequest $url -OutFile $destino -UseBasicParsing -ErrorAction Stop
        # Um 404 tambem "baixa com sucesso": o corpo vira o arquivo. Um .ps1
        # que comeca com "404" nao da erro util nenhum quando executado.
        $primeira = (Get-Content $destino -TotalCount 1 -ErrorAction SilentlyContinue)
        if ($primeira -match "^\s*404" ) { Remove-Item $destino -Force; return $false }
        return $true
    }
    catch { return $false }
}

function Onde-Procurei() {
    Write-Host "        Procurei no PATH, no registro do Windows e em:" -ForegroundColor DarkGray
    foreach ($p in $script:ultimaBusca) { Write-Host "          $p" -ForegroundColor DarkGray }
}

Passo 1 "Conferindo git e python"

if ($Git -and (Testar-Programa $Git)) { $git = $Git }
else {
    $git = Achar-Programa "git" @(
        "$env:ProgramFiles\Git\cmd\git.exe",
        "$env:ProgramFiles\Git\bin\git.exe",
        "${env:ProgramFiles(x86)}\Git\cmd\git.exe",
        "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe",
        # GitHub Desktop traz o proprio git, e muita gente so tem esse
        "$env:LOCALAPPDATA\GitHubDesktop\app-*\resources\app\git\cmd\git.exe",
        "$env:USERPROFILE\scoop\shims\git.exe",
        "$env:ProgramData\chocolatey\bin\git.exe",
        "D:\Git\cmd\git.exe"
    ) (Caminhos-Do-Registro @("HKLM:\SOFTWARE\GitForWindows",
                             "HKCU:\SOFTWARE\GitForWindows") "cmd\git.exe")
}
# Sem git NAO se aborta. O git serve para TRAZER codigo novo; o bot roda
# sem ele. Quem publica no WhatsApp e o whatsapp_queue_sender.py, filho do
# startup.py, e nenhum dos dois toca em git (so core/site_publisher.py e
# core/papel.py tocam, e os dois toleram a ausencia). Tratar git como
# obrigatorio foi erro meu de desenho: deixava o Daniel sem publicar por
# causa de uma ferramenta que a publicacao nao usa.
if (-not $git) {
    Aviso "nao achei um 'git' que funcione — vou seguir com o codigo que ja esta no disco."
    Onde-Procurei
    Write-Host "        O bot NAO precisa de git para publicar; ele so nao vai receber" -ForegroundColor DarkGray
    Write-Host "        as correcoes novas agora. Para trazer o codigo depois:" -ForegroundColor DarkGray
    Write-Host "          .\aplicar_tudo.ps1 -Git `"C:\caminho\git.exe`"" -ForegroundColor DarkGray
    $problemas++
}
else { Ok "git em $git" }

if ($Python -and (Testar-Programa $Python)) { $python = $Python }
else {
    $python = Achar-Programa "python" @(
        "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
        "$env:ProgramFiles\Python*\python.exe",
        "C:\Python*\python.exe",
        "D:\Python*\python.exe",
        # O Python DA LOJA de verdade (nao o atalho): fica em WindowsApps
        # numa pasta PythonSoftwareFoundation.*, e esse executa.
        "$env:LOCALAPPDATA\Microsoft\WindowsApps\PythonSoftwareFoundation.Python*\python.exe",
        "$env:ProgramFiles\WindowsApps\PythonSoftwareFoundation.Python*\python.exe"
    ) (Caminhos-Do-Registro @("HKLM:\SOFTWARE\Python\PythonCore",
                             "HKCU:\SOFTWARE\Python\PythonCore") "InstallPath\python.exe")
}
if (-not $python) {
    Erro "nao achei um 'python' que funcione."
    Onde-Procurei
    Write-Host "        'Python was not found... Microsoft Store' significa que o que" -ForegroundColor DarkGray
    Write-Host "        esta no PATH e um ATALHO, nao o Python." -ForegroundColor DarkGray
    Write-Host "        Se voce sabe onde ele esta, passe o caminho:" -ForegroundColor DarkGray
    Write-Host "          .\aplicar_tudo.ps1 -SemAgenda -Python `"C:\caminho\python.exe`"" -ForegroundColor DarkGray
    exit 1
}
Ok "python em $python"

# Os scripts filhos (start.ps1, stop.ps1, configurar_ciclo.ps1) fazem a
# propria busca por `python` e cairiam no mesmo atalho da Loja. Pondo as
# pastas encontradas na frente do PATH DESTE PROCESSO, eles herdam a escolha
# certa. E do processo: nada e gravado no registro nem no ambiente do
# usuario (Regra 10 — nao alterar configuracao da maquina).
$env:PATH = (Split-Path $python) + ";" + $env:PATH
if ($git) { $env:PATH = (Split-Path $git) + ";" + $env:PATH }

# ------------------------------------------------------- rodada em curso
Passo 2 "Conferindo se ha publicacao em andamento (Regra 10)"
$emAndamento = & $python -c "from core.database import execucao_em_andamento; print('SIM' if execucao_em_andamento() else 'NAO')" 2>$null
if ($emAndamento -eq "SIM") {
    Erro "ha uma rodada de publicacao em andamento."
    Write-Host "        Espere ela terminar e rode de novo — parar agora corta um" -ForegroundColor DarkGray
    Write-Host "        envio pela metade." -ForegroundColor DarkGray
    exit 1
}
elseif ($emAndamento -eq "NAO") { Ok "nenhuma rodada em andamento" }
else { Aviso "nao consegui perguntar ao banco (codigo antigo?) — seguindo" }

# ------------------------------------------------------------- parar bot
Passo 3 "Parando o bot"
& (Join-Path $BASE "stop.ps1")

# ------------------------------------------------------------ traz o codigo
if (-not $git) {
    Passo 4 "Trazendo a branch: PULADO (sem git) — seguindo com o codigo do disco"
}
else {
Passo 4 "Trazendo a branch $Branch"
$sujo = & $git status --porcelain
if ($sujo) {
    $marca = "aplicar_tudo-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Aviso "ha alteracoes locais nao commitadas — guardando em '$marca'"
    Write-Host "        Recupere depois com: git stash list ; git stash pop" -ForegroundColor DarkGray
    & $git stash push -u -m $marca | Out-Null
}
& $git fetch origin $Branch
if ($LASTEXITCODE -ne 0) { Erro "git fetch falhou (sem internet? sem credencial?)"; exit 1 }
& $git checkout $Branch
if ($LASTEXITCODE -ne 0) { Erro "git checkout falhou"; exit 1 }
& $git pull --ff-only origin $Branch | Out-Null
Ok "codigo em $(& $git rev-parse --short HEAD)"
}

# ------------------------------------------------------- dependencias
# Um Python recem-instalado nao tem NADA: nem python-telegram-bot, nem
# playwright, nem pyautogui. O passo seguinte (import real) falharia com
# ModuleNotFoundError e pareceria erro de codigo. Achado em 08/09/2026: a
# limpeza do sistema levou o Python inteiro, entao reinstalar significa
# comecar do zero tambem nas dependencias.
if ($SemDependencias) {
    Passo 5 "Dependencias: pulado (-SemDependencias)"
}
else {
    Passo 5 "Conferindo as dependencias do bot"

    # Sem git, o requirements.txt do disco e o ANTIGO — e e justamente nele
    # que faltava o pywin32, de quem vem o win32clipboard. Instalar a lista
    # velha devolveria o Telegram e deixaria o WhatsApp mudo do mesmo jeito
    # (a foto nao chega ao clipboard e o envio e abortado pela Regra 5).
    # Entao, sem git, busco SO esta lista da branch — um arquivo de texto,
    # nao codigo executavel.
    $listaReq = Join-Path $BASE "requirements.txt"
    if (-not $git) {
        $urlReq = "https://raw.githubusercontent.com/bot-ofertas/bot-ofertas.github.io/$Branch/requirements.txt"
        $tmpReq = Join-Path $env:TEMP "requirements_branch.txt"
        try {
            Invoke-WebRequest $urlReq -OutFile $tmpReq -UseBasicParsing -ErrorAction Stop
            # So aceito se vier a lista de verdade: um 404 salvo em arquivo
            # tambem "baixa com sucesso", e instalar uma pagina de erro nao
            # da erro util nenhum.
            if ((Get-Content $tmpReq -Raw) -match "(?m)^pywin32") {
                $listaReq = $tmpReq
                Ok "lista de pacotes atualizada trazida da branch (sem git)"
            }
            else { Aviso "o requirements.txt baixado nao parece valido — usando o do disco" }
        }
        catch { Aviso "nao consegui baixar a lista atualizada ($($_.Exception.Message)) — usando a do disco" }
    }

    & $python -c "import telegram, playwright, dotenv, psutil, win32clipboard" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Aviso "faltam pacotes — instalando (pode levar alguns minutos)"
        & $python -m pip install --disable-pip-version-check -q -r $listaReq
        if ($LASTEXITCODE -ne 0) { Erro "pip install falhou"; $problemas++ }
        else {
            # O playwright instala a biblioteca, mas o NAVEGADOR vem
            # separado — sem ele a raspagem do Mercado Livre nao roda.
            # O pywin32 instala DLLs (pywintypes*.dll, pythoncom*.dll) que
            # precisam ser registradas. Sem isso o `import win32clipboard`
            # falha com "DLL load failed" mesmo com o pacote instalado — e o
            # sintoma seria de novo o WhatsApp mudo, agora com o pacote na
            # lista. Rodar o postinstall e barato e idempotente.
            & $python -c "import win32clipboard" 2>$null
            if ($LASTEXITCODE -ne 0) {
                Aviso "pywin32 instalado mas nao importa — rodando o postinstall"
                & $python -m pywin32_postinstall -install 2>$null
                & $python -c "import win32clipboard" 2>$null
                if ($LASTEXITCODE -ne 0) {
                    Erro "win32clipboard nao importa — o WhatsApp NAO vai conseguir anexar a foto"
                    Write-Host "        (a Regra 5 aborta o envio sem a foto, entao nada sairia no grupo)" -ForegroundColor DarkGray
                    Write-Host "        Tente numa janela de Administrador: python -m pywin32_postinstall -install" -ForegroundColor DarkGray
                    $problemas++
                }
                else { Ok "pywin32 registrado" }
            }

            & $python -m playwright install chromium
            if ($LASTEXITCODE -ne 0) { Aviso "playwright install chromium falhou — a raspagem do ML pode nao rodar"; $problemas++ }
            else { Ok "dependencias e Chromium instalados" }
        }
    }
    else { Ok "dependencias ja presentes" }
}

# ------------------------------------------------------------- validacao
Passo 6 "Validando o codigo (compile + import real — Regra 2)"

# A lista e filtrada pelo que EXISTE no disco. `core/papel.py` e
# `core/segredos.py` sao arquivos novos da branch: sem git eles nao foram
# trazidos, e validar um arquivo inexistente derrubava o script com
# "[Errno 2] No such file or directory: 'core/papel.py'" — travando o bot
# por causa de um arquivo que ele nem usa ainda (visto no PC do Daniel,
# 08/09/2026). O que importa validar e o que vai rodar.
$paraCompilar = @()
$paraImportar = @()
foreach ($par in @(
        @("startup.py",        "startup"),
        @("rastreador.py",     "rastreador"),
        @("core/janela.py",    "core.janela"),
        @("core/papel.py",     "core.papel"),
        @("core/segredos.py",  "core.segredos"),
        @("core/database.py",  "core.database"),
        @("integrations/whatsapp_sender.py", "integrations.whatsapp_sender"))) {
    if (Test-Path (Join-Path $BASE $par[0])) {
        $paraCompilar += $par[0]
        $paraImportar += $par[1]
    }
}
Write-Host "  validando $($paraCompilar.Count) arquivo(s) presentes" -ForegroundColor DarkGray

& $python -m py_compile @paraCompilar
if ($LASTEXITCODE -ne 0) { Erro "py_compile falhou"; exit 1 }
& $python -c "import $($paraImportar -join ', ')"
if ($LASTEXITCODE -ne 0) { Erro "import real falhou — o bot nao subiria"; exit 1 }
Ok "compila e importa"

# ------------------------------------------------------------------ .env
Passo 7 "Conferindo o .env"
if (-not (Test-Path (Join-Path $BASE ".env"))) {
    Erro ".env nao existe — o bot nao tem token nem canal."
    exit 1
}
$env_txt = Get-Content (Join-Path $BASE ".env") -Raw
foreach ($chave in @("TOKEN_TELEGRAM", "CANAL_GERAL", "WHATSAPP_GROUP_ID")) {
    if ($env_txt -notmatch "(?m)^\s*$chave\s*=\s*\S") { Erro "$chave vazio ou ausente"; $problemas++ }
    else { Ok "$chave definido" }
}
# Sem WHATSAPP_GROUP_NAME a automacao procura pelo texto literal
# "Bot-Ofertas" na lista de conversas. Se o grupo tem outro nome, a busca
# nao acha nada, o envio falha e o motivo nao aparece em lugar nenhum —
# principal suspeito das 17 falhas de wa_silencioso.envio.
if ($env_txt -notmatch "(?m)^\s*WHATSAPP_GROUP_NAME\s*=\s*\S") {
    Aviso "WHATSAPP_GROUP_NAME ausente — a busca vai usar o literal 'Bot-Ofertas'."
    Write-Host "        Se o grupo tem outro nome, acrescente no .env:" -ForegroundColor DarkGray
    Write-Host "          WHATSAPP_GROUP_NAME=<nome exato do grupo>" -ForegroundColor DarkGray
}
else { Ok "WHATSAPP_GROUP_NAME definido" }

# ----------------------------------------------------------------- agenda
if ($SemAgenda) {
    Passo 8 "Agendador: pulado (-SemAgenda)"
}
else {
    Passo 8 "Registrando o ciclo diario (liga 08:30 / desliga 02:00)"
    $admin = ([Security.Principal.WindowsPrincipal] `
              [Security.Principal.WindowsIdentity]::GetCurrent()
             ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        Erro "esta janela NAO e de Administrador."
        Write-Host "        Registrar tarefas do Agendador exige elevacao. Feche," -ForegroundColor DarkGray
        Write-Host "        abra o PowerShell como Administrador e rode de novo —" -ForegroundColor DarkGray
        Write-Host "        ou use .\aplicar_tudo.ps1 -SemAgenda para so subir o bot." -ForegroundColor DarkGray
        $problemas++
    }
    else {
        if (-not $git) {
            # Sem git nada disso foi trazido; e `configurar_ciclo.ps1` nem
            # existe na main, entao a chamada abaixo falharia por arquivo
            # inexistente.
            foreach ($arq in @("configurar_ciclo.ps1", "agendar_shutdown.ps1",
                               "aguardar_e_desligar.ps1", "acordar_e_iniciar.ps1",
                               "garantir_bot.py")) {
                if (Buscar-DaBranch $arq) { Write-Host "  trazido: $arq" -ForegroundColor DarkGray }
                else { Aviso "nao consegui trazer $arq — o ciclo pode ficar incompleto"; $problemas++ }
            }
        }
        & (Join-Path $BASE "configurar_ciclo.ps1") -SemGit
        if ($LASTEXITCODE -ne 0) { Erro "configurar_ciclo.ps1 terminou com erro"; $problemas++ }
        else { Ok "ciclo diario registrado" }
    }
}

# ------------------------------------------------------------------ subir
Passo 9 "Subindo o bot (processo PAI — Regra 10)"
& (Join-Path $BASE "start.ps1")

# "Chamar Popen nao e o mesmo que ter subido" (Regra 15): com .env invalido
# o startup.py sai em ~1s. Confirma depois da carencia, nao antes.
Start-Sleep -Seconds 10
$vivo = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*startup.py*" }
if ($vivo) { Ok "startup.py vivo (PID $($vivo[0].ProcessId))" }
else {
    Erro "startup.py nao sobreviveu aos 10s de carencia."
    Write-Host "        Motivo provavel no fim deste arquivo:" -ForegroundColor DarkGray
    Write-Host "          data\startup_full.log.err" -ForegroundColor DarkGray
    $problemas++
}

# ---------------------------------------------------------------- resumo
Write-Host ""
if ($problemas -eq 0) {
    Write-Host "TUDO APLICADO. O bot esta no ar e o ciclo diario registrado." -ForegroundColor Green
}
else {
    Write-Host "APLICADO COM $problemas PROBLEMA(S) — veja os ERRO acima." -ForegroundColor Yellow
}
Write-Host "Status:  .\status.ps1"
Write-Host "Logs:    Get-Content data\bot.log -Tail 30 -Wait"
exit $problemas
