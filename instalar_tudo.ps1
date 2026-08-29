# instalar_tudo.ps1
# Um comando para deixar bot + n8n + ciclo diário funcionando.
#
# Existe porque a instalação virou uma sequência de oito passos manuais em
# que cada um podia falhar em silêncio — e travou justamente no mais frágil:
# achar a tela da API key numa interface que muda de versão para versão.
# Aqui, se a chave existir a importação vai pela API; se não existir, o
# script cai sozinho no caminho por arquivo, que não precisa de chave
# nenhuma. Nos dois casos o resultado é o mesmo.
#
# Cada etapa é independente: uma que falhe não impede as seguintes, e o
# resumo do fim diz exatamente o que ficou pendente. O oposto — abortar no
# primeiro erro — deixaria o ciclo diário desconfigurado por causa de um
# problema no n8n, que são coisas sem relação nenhuma.
#
# Uso:
#   .\instalar_tudo.ps1                 # tudo (sem reiniciar o bot)
#   .\instalar_tudo.ps1 -ReiniciarBot   # também reinicia, esperando o bot ficar ocioso
#   .\instalar_tudo.ps1 -SemGit         # não faz git pull

param(
    [switch]$ReiniciarBot,
    [switch]$SemGit
)

$ErrorActionPreference = "Continue"
$BASE = $PSScriptRoot
Set-Location $BASE

$pendencias = New-Object System.Collections.Generic.List[string]
$feitos = New-Object System.Collections.Generic.List[string]

function Titulo($n, $texto) {
    Write-Host ""
    Write-Host "──────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host " [$n] $texto" -ForegroundColor Cyan
    Write-Host "──────────────────────────────────────────────────────────" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  BOT OFERTAS — instalacao completa" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Write-Host "`nERRO: python nao esta no PATH. Instale o Python e rode de novo." -ForegroundColor Red
    exit 1
}

# ─── 1. Código atualizado ────────────────────────────────────────────────
Titulo 1 "Atualizando o codigo"
if ($SemGit) {
    Write-Host "  pulado (-SemGit)" -ForegroundColor DarkGray
}
else {
    $branch = "claude/bot-ofertas-n8n-8d7qe2"
    git pull origin $branch 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    if ($LASTEXITCODE -eq 0) { $feitos.Add("codigo atualizado ($branch)") }
    else {
        # Causa mais comum: alteração local no .env ou em algum script. Não
        # é motivo para parar — o resto da instalação usa o código que já
        # está no disco e funciona igual.
        Write-Host "  git pull falhou — seguindo com o codigo local." -ForegroundColor Yellow
        $pendencias.Add("git pull falhou (rode 'git status' para ver o que trava)")
    }
}

# ─── 2. .env ─────────────────────────────────────────────────────────────
Titulo 2 "Preenchendo o .env (gera N8N_TOKEN, descobre ADMIN_CHAT_ID)"
& $python (Join-Path $BASE "n8n\setup_n8n.py") --configurar
$envOk = ($LASTEXITCODE -eq 0)
if ($envOk) { $feitos.Add(".env completo") }

# Relê o .env do disco: o --configurar acabou de escrever nele, e este
# processo do PowerShell não enxerga isso sozinho.
$envVals = @{}
if (Test-Path (Join-Path $BASE ".env")) {
    Get-Content (Join-Path $BASE ".env") | ForEach-Object {
        if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') {
            $envVals[$Matches[1]] = $Matches[2].Trim()
        }
    }
}
$temApiKey = -not [string]::IsNullOrWhiteSpace($envVals["N8N_API_KEY"])
$temChatId = -not [string]::IsNullOrWhiteSpace($envVals["ADMIN_CHAT_ID"])

if (-not $temChatId) {
    $pendencias.Add("ADMIN_CHAT_ID vazio — mande /start para o seu bot no Telegram e rode este script de novo (sem ele, NENHUM alerta chega)")
}

# ─── 3. Workflows no n8n ─────────────────────────────────────────────────
Titulo 3 "Instalando os workflows no n8n"
$importado = $false

if ($temApiKey) {
    Write-Host "  N8N_API_KEY presente — importando pela API." -ForegroundColor Green
    & $python (Join-Path $BASE "n8n\setup_n8n.py") --importar
    if ($LASTEXITCODE -eq 0) {
        $importado = $true
        $feitos.Add("5 workflows importados e ativados pela API")
    }
    else {
        Write-Host "  A importacao pela API falhou — caindo no caminho por arquivo." -ForegroundColor Yellow
    }
}
else {
    Write-Host "  Sem N8N_API_KEY — usando o caminho que nao precisa de chave." -ForegroundColor Yellow
}

if (-not $importado) {
    $prontos = Join-Path $BASE "n8n\prontos"
    & $python (Join-Path $BASE "n8n\setup_n8n.py") --preparar $prontos | Out-Null

    # A CLI do n8n importa credenciais E workflows sem API key nenhuma. O
    # problema é achá-la: `Get-Command n8n` só enxerga o PATH, e um n8n
    # instalado por npm no Windows deixa o executável em %APPDATA%\npm, que
    # muitas vezes não está lá; e um n8n em Docker não tem executável nenhum
    # na máquina. Procurar nos quatro lugares é a diferença entre automatizar
    # tudo e mandar a pessoa clicar cinco vezes na interface.
    $cli = $null

    $doPath = (Get-Command n8n -ErrorAction SilentlyContinue).Source
    if ($doPath) { $cli = @{ Modo = "exe"; Exe = $doPath } }

    if (-not $cli) {
        foreach ($cand in @("$env:APPDATA\npm\n8n.cmd", "$env:APPDATA\npm\n8n",
                            "$env:ProgramFiles\nodejs\n8n.cmd")) {
            if (Test-Path $cand) { $cli = @{ Modo = "exe"; Exe = $cand }; break }
        }
    }

    if (-not $cli) {
        # Contêiner do n8n rodando: o executável vive dentro dele.
        $docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
        if ($docker) {
            $nome = (& $docker ps --format "{{.Names}}`t{{.Image}}" 2>$null |
                     Where-Object { $_ -match "n8n" } |
                     ForEach-Object { ($_ -split "`t")[0] } | Select-Object -First 1)
            if ($nome) { $cli = @{ Modo = "docker"; Exe = $docker; Container = $nome } }
        }
    }

    if ($cli) {
        Write-Host ("  CLI do n8n encontrada ({0}) — importando credenciais e workflows." -f $cli.Modo) -ForegroundColor Green

        # As credenciais saem do .env com os segredos EM CLARO, porque é
        # assim que o `import:credentials` os recebe. Pasta temporária do
        # usuário, apagada no finally — nunca perto do repositório.
        $credFile = Join-Path ([System.IO.Path]::GetTempPath()) ("botofertas_cred_" + [guid]::NewGuid().ToString("N") + ".json")
        try {
            & $python (Join-Path $BASE "n8n\setup_n8n.py") --preparar-credenciais $credFile | Out-Null
            $temCred = Test-Path $credFile

            if ($cli.Modo -eq "docker") {
                # Dentro do contêiner o disco é outro: os arquivos precisam
                # atravessar antes de existir para a CLI.
                & $cli.Exe exec $cli.Container mkdir -p /tmp/botofertas 2>&1 | Out-Null
                & $cli.Exe cp "$prontos/." "$($cli.Container):/tmp/botofertas/" 2>&1 | Out-Null
                if ($temCred) { & $cli.Exe cp $credFile "$($cli.Container):/tmp/botofertas_cred.json" 2>&1 | Out-Null }
                if ($temCred) {
                    & $cli.Exe exec $cli.Container n8n import:credentials --input=/tmp/botofertas_cred.json 2>&1 |
                        ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
                }
                & $cli.Exe exec $cli.Container n8n import:workflow --separate --input=/tmp/botofertas 2>&1 |
                    ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
                $okImport = ($LASTEXITCODE -eq 0)
                & $cli.Exe exec $cli.Container rm -f /tmp/botofertas_cred.json 2>&1 | Out-Null
            }
            else {
                if ($temCred) {
                    & $cli.Exe import:credentials --input="$credFile" 2>&1 |
                        ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
                }
                & $cli.Exe import:workflow --separate --input="$prontos" 2>&1 |
                    ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
                $okImport = ($LASTEXITCODE -eq 0)
            }

            if ($okImport) {
                $importado = $true
                $feitos.Add("2 credenciais + 5 workflows importados pela CLI do n8n")
            }
        }
        finally {
            # O arquivo tem o token do Telegram legível. Sai daqui de
            # qualquer jeito, inclusive se a importação estourar no meio.
            if (Test-Path $credFile) { Remove-Item $credFile -Force -ErrorAction SilentlyContinue }
        }

        if ($importado) {
            # A CLI importa desativado. Ativar apenas os NOSSOS: um
            # `--all` ligaria também qualquer outro workflow que o Daniel
            # tenha na instância, o que não é decisão deste script.
            Write-Host "  Ativando os workflows do Bot Ofertas..." -ForegroundColor Yellow
            $lista = if ($cli.Modo -eq "docker") {
                & $cli.Exe exec $cli.Container n8n list:workflow 2>$null
            } else { & $cli.Exe list:workflow 2>$null }

            $ativados = 0
            foreach ($linha in $lista) {
                if ($linha -match "Bot Ofertas") {
                    $id = ($linha -split "\|")[0].Trim()
                    if ($id) {
                        if ($cli.Modo -eq "docker") {
                            & $cli.Exe exec $cli.Container n8n update:workflow --id=$id --active=true 2>&1 | Out-Null
                        } else {
                            & $cli.Exe update:workflow --id=$id --active=true 2>&1 | Out-Null
                        }
                        if ($LASTEXITCODE -eq 0) { $ativados++ }
                    }
                }
            }
            if ($ativados -gt 0) {
                $feitos.Add("$ativados workflow(s) ativados")
                Write-Host "  $ativados workflow(s) ativados." -ForegroundColor Green
            }
            else {
                $pendencias.Add("ative os workflows do Bot Ofertas no n8n (botao Active) — a importacao funcionou, a ativacao automatica nao")
            }
            $pendencias.Add("REINICIE o n8n para ele carregar os workflows importados (a CLI escreve no banco; a instancia ja rodando nao releem sozinha)")
        }
        else {
            Write-Host "  A importacao pela CLI falhou." -ForegroundColor Yellow
        }
    }

    if (-not $importado) {
        Write-Host "  Arquivos prontos em: $prontos" -ForegroundColor Green
        Write-Host "  Importe por: Workflows -> ... -> Import from File" -ForegroundColor Green
        $feitos.Add("workflows preparados em n8n\prontos (importar a mao)")
        $pendencias.Add("importar os 5 arquivos de n8n\prontos no n8n, criar as 2 credenciais e ativar")
    }
}

# ─── 4. Ciclo diário ─────────────────────────────────────────────────────
Titulo 4 "Registrando o ciclo diario (liga 08:30 / desliga 02:00)"
& (Join-Path $BASE "agendar_shutdown.ps1")
if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) {
    $feitos.Add("ciclo diario agendado (4 tarefas)")
}
else {
    $pendencias.Add("agendar_shutdown.ps1 falhou — rode sozinho para ver o erro")
}

# ─── 5. Bot ──────────────────────────────────────────────────────────────
Titulo 5 "Bot"
if ($ReiniciarBot) {
    # Regra 10 do CLAUDE.md: nunca derrubar no meio de uma rodada, e sempre
    # matar/relancar o processo PAI — reiniciar so os filhos esgota o
    # contador de tentativas do supervisor e derruba o bot em silencio.
    Write-Host "  Esperando o bot ficar ocioso (ate 5 min)..." -ForegroundColor Yellow
    $limite = (Get-Date).AddMinutes(5)
    do {
        & $python (Join-Path $BASE "verificar_ocioso.py") | Out-Null
        $ocioso = ($LASTEXITCODE -eq 0)
        if (-not $ocioso) { Start-Sleep -Seconds 20 }
    } while (-not $ocioso -and (Get-Date) -lt $limite)

    if (-not $ocioso) {
        Write-Host "  Ainda ocupado depois de 5 min — NAO vou reiniciar agora." -ForegroundColor Yellow
        $pendencias.Add("reiniciar o bot depois (rodada em andamento durante a instalacao)")
    }
    else {
        Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match "startup\.py|rastreador.*\.py|campanha_ferramentas\.py|whatsapp_queue_sender\.py" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
        Start-Process -FilePath $python -ArgumentList "-u", (Join-Path $BASE "startup.py") `
            -WorkingDirectory $BASE -WindowStyle Hidden
        Write-Host "  startup.py relancado (processo pai)." -ForegroundColor Green
        $feitos.Add("bot reiniciado pelo processo pai")
    }
}
else {
    # Sem -ReiniciarBot, garante ao menos que ele esteja de pe — o mesmo
    # supervisor que roda de 30 em 30 min, so que agora.
    & $python (Join-Path $BASE "garantir_bot.py")
    $feitos.Add("bot verificado (garantir_bot.py)")
    Write-Host "  (o bot NAO foi reiniciado; use -ReiniciarBot para carregar o codigo novo)" -ForegroundColor DarkGray
}

# ─── Resumo ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  RESUMO" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Feito:" -ForegroundColor Green
foreach ($f in $feitos) { Write-Host "  OK  $f" -ForegroundColor Green }

if ($pendencias.Count -gt 0) {
    Write-Host ""
    Write-Host "Falta voce fazer:" -ForegroundColor Yellow
    $i = 1
    foreach ($p in $pendencias) { Write-Host "  $i. $p" -ForegroundColor Yellow; $i++ }
}
else {
    Write-Host ""
    Write-Host "Nada pendente." -ForegroundColor Green
}

Write-Host ""
Write-Host "Conferir:  .\status.ps1                    (estado do bot)" -ForegroundColor DarkGray
Write-Host "           .\agendar_shutdown.ps1 -Status  (ciclo diario)" -ForegroundColor DarkGray
Write-Host ""
