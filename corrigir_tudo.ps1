# corrigir_tudo.ps1 -- resgate: para o bot inteiro, traz o codigo novo e sobe.
#
# Existe porque o aplicar_tudo.ps1 pode ficar INALCANCAVEL. Ele roda a partir
# do disco, entao uma correcao que so existe no repositorio nao ajuda em nada
# enquanto o script local nao for atualizado -- e se o passo 2 dele bloquear
# (rodada em andamento), a atualizacao nunca acontece. Foi o circulo em que o
# Daniel ficou preso em 20/09/2026: a correcao das janelas pretas estava
# pronta e nao tinha como chegar na maquina.
#
# Este script nao depende de NENHUM outro arquivo do projeto. Ele mata os
# processos pela lista que traz dentro de si, entao funciona mesmo com um
# stop.ps1 antigo e incompleto no disco.
#
# Uso (PowerShell como Administrador):
#   cd D:\bot_ofertas
#   powershell -ExecutionPolicy Bypass -File .\corrigir_tudo.ps1

param(
    [string]$Branch = "claude/bot-ofertas-n8n-8d7qe2",
    [string]$Base   = $PSScriptRoot
)

if (-not $Base) { $Base = "D:\bot_ofertas" }
Set-Location $Base

function Passo($n, $t) { Write-Host "`n[$n] $t" -ForegroundColor Cyan }
function Ok($t)        { Write-Host "  OK: $t"    -ForegroundColor Green }
function Aviso($t)     { Write-Host "  AVISO: $t" -ForegroundColor Yellow }
function Erro($t)      { Write-Host "  ERRO: $t"  -ForegroundColor Red }

# Os quatro filhos que o startup.py sobe, mais o pai. Lista literal de
# proposito: "rastreador.py" NAO casa com "rastreador_amazon.py", e foi
# exatamente esse engano que deixou tres orfaos vivos a cada "Bot parado."
$PADROES = @(
    "startup.py",
    "rastreador.py",
    "rastreador_amazon.py",
    "campanha_ferramentas.py",
    "whatsapp_queue_sender.py"
)

function Processos-Do-Bot {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $linha = $_.CommandLine
        $linha -and ($PADROES | Where-Object { $linha -like "*$_*" })
    }
}

# ---------------------------------------------------------------- 1. parar
Passo 1 "Parando o bot inteiro (pai + 4 filhos)"
$antes = @(Processos-Do-Bot)
if ($antes.Count -eq 0) {
    Ok "nada rodando"
}
else {
    foreach ($p in $antes) {
        Write-Host ("  encerrando PID {0}  {1}" -f $p.ProcessId, $p.Name) -ForegroundColor DarkGray
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
    $sobrou = @(Processos-Do-Bot)
    if ($sobrou.Count -gt 0) {
        Aviso "$($sobrou.Count) processo(s) resistiram:"
        foreach ($p in $sobrou) {
            Write-Host ("    PID {0}  {1}" -f $p.ProcessId, $p.CommandLine) -ForegroundColor DarkGray
        }
        Write-Host "    Encerre no Gerenciador de Tarefas e rode de novo." -ForegroundColor DarkGray
    }
    else {
        Ok "$($antes.Count) processo(s) encerrado(s), nenhum sobrou"
    }
}

# ----------------------------------------------------------------- 2. git
Passo 2 "Trazendo o codigo novo"
$git = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $git) {
    Erro "git nao esta no PATH. Instale com: winget install --id Git.Git"
    exit 1
}

# A pasta pode estar recusada por pertencer a outra conta do Windows
# (protecao do git contra CVE-2022-24765). Marcar SO esta pasta como
# confiavel e uma configuracao do git, nao do sistema.
$teste = & $git rev-parse --is-inside-work-tree 2>&1
if ($LASTEXITCODE -ne 0) {
    $texto = ($teste | Out-String)
    if ($texto -match "dubious ownership" -or $texto -match "safe\.directory") {
        $caminho = $Base -replace '\\', '/'
        Aviso "o git recusa esta pasta (pertence a outra conta). Liberando so ela."
        & $git config --global --add safe.directory $caminho
    }
    else {
        Erro "o git nao consegue ler esta pasta:"
        Write-Host $texto -ForegroundColor DarkGray
        exit 1
    }
}

$sujo = & $git status --porcelain
if ($sujo) {
    $marca = "corrigir_tudo-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Aviso "ha alteracoes locais - guardando em '$marca'"
    Write-Host "    Recupere depois com: git stash list ; git stash pop" -ForegroundColor DarkGray
    & $git stash push -u -m $marca | Out-Null
}

$saida = & $git fetch origin $Branch 2>&1
if ($LASTEXITCODE -ne 0) {
    Erro "git fetch falhou. O git disse:"
    Write-Host ($saida | Out-String) -ForegroundColor DarkGray
    exit 1
}
& $git checkout $Branch 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Erro "git checkout falhou"; exit 1 }
# merge --ff-only, NAO `reset --hard`: reset descartaria commit local sem
# perguntar. Aqui o script atualiza o disco de alguem, nao o meu -- perder
# trabalho dele para consertar uma janela e um preco que nao vale a pena.
& $git merge --ff-only "origin/$Branch" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Erro "a branch local divergiu da do servidor - nao vou apagar nada."
    Write-Host "    Me mande a saida de:  git log --oneline -5" -ForegroundColor DarkGray
    Write-Host "    e de:                 git status" -ForegroundColor DarkGray
    exit 1
}
Ok "codigo em $(& $git rev-parse --short HEAD) ($Branch)"

# ------------------------------------------------------------- 3. subir
Passo 3 "Subindo o bot (processo PAI, sem janela)"
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { Erro "python nao esta no PATH."; exit 1 }

$log = Join-Path $Base "data\startup_full.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

# -WindowStyle Hidden: o pai ganha um console OCULTO, e os filhos herdam
# esse console em vez de cada um abrir o seu. Junto com o CREATE_NO_WINDOW
# que o startup.py agora usa, nenhuma janela preta aparece.
Start-Process -FilePath $python `
    -ArgumentList "-u", "startup.py" `
    -WorkingDirectory $Base `
    -RedirectStandardOutput $log `
    -RedirectStandardError "$log.err" `
    -WindowStyle Hidden

Start-Sleep -Seconds 8

# ----------------------------------------------------------- 4. conferir
Passo 4 "Conferindo"
$depois = @(Processos-Do-Bot)
if ($depois.Count -eq 0) {
    Erro "o bot nao subiu. Veja o fim do log:"
    if (Test-Path $log) { Get-Content $log -Tail 15 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
    if (Test-Path "$log.err") { Get-Content "$log.err" -Tail 15 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } }
    exit 1
}
Ok "$($depois.Count) processo(s) do bot no ar:"
foreach ($p in $depois) {
    $qual = ($PADROES | Where-Object { $p.CommandLine -like "*$_*" } | Select-Object -First 1)
    Write-Host ("    PID {0}  {1}" -f $p.ProcessId, $qual) -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "PRONTO. As janelas pretas que ainda estiverem abertas sao as" -ForegroundColor Green
Write-Host "antigas, do codigo velho: feche na mao, uma vez so." -ForegroundColor Green
