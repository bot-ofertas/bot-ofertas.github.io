# empacotar_projeto.ps1 - junta a pasta inteira num punhado de arquivos de
# texto que cabem num upload de conversa.
#
# POR QUE ISSO EXISTE
# O seletor "Adicionar pasta" so funciona em sessao LOCAL do Claude Code.
# Numa sessao na nuvem ele fica cinza, e nao existe como mandar uma pasta.
# Mandar arquivo por arquivo nao escala: D:\bot_ofertas tem dezenas.
# Este script resolve pelo outro lado: transforma a pasta num documento so
# (ou em partes de ~3 MB), com a arvore de diretorios no topo e o conteudo
# de cada arquivo em seguida, pronto para anexar.
#
# O QUE ELE NUNCA INCLUI (Regra 10 e o vazamento de 2026-09-07)
#   - .env e .env.* : registra so QUAIS chaves existem e se estao
#     preenchidas. O valor nunca sai do disco;
#   - data\ml_profile\ : perfil de navegador com a sessao logada do ML;
#   - qualquer arquivo com token/cookie/session/credential no nome;
#   - banco (.db/.sqlite), .pyc, binarios, node_modules, .venv, .git.
# O que sobra ainda passa pela redacao - os logs antigos TEM o token do
# Telegram dentro, e copiar o log cru so mudaria o vazamento de lugar.
#
# Uso:
#   .\empacotar_projeto.ps1                  -> codigo-fonte (padrao)
#   .\empacotar_projeto.ps1 -ComLogs         -> inclui data\*.log e *.jsonl
#   .\empacotar_projeto.ps1 -MaxMB 2         -> partes menores

param(
    [switch]$ComLogs,
    [double]$MaxMB = 3.0,
    [string]$Pasta = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Pasta)) { $Pasta = $PSScriptRoot }
if ([string]::IsNullOrWhiteSpace($Pasta)) { $Pasta = (Get-Location).Path }
if (-not (Test-Path $Pasta)) { Write-Host "Pasta nao encontrada: $Pasta" -ForegroundColor Red; exit 1 }
$Pasta = (Resolve-Path $Pasta).Path

$Desktop = [Environment]::GetFolderPath("Desktop")
$Prefixo = "bot_ofertas_codigo"

# ---------------------------------------------------------------- redacao
# Mesmos padroes de coletar_diagnostico.ps1, mais a forma CHAVE=valor: num
# dump de codigo pode aparecer segredo embutido em arquivo de exemplo.
function Redigir([string]$texto) {
    if (-not $texto) { return $texto }
    $texto = $texto -replace '\d{8,12}:AA[\w-]{30,}', '***REDIGIDO***'
    $texto = $texto -replace 'sk-ant-api\d{2}-[\w-]{20,}', '***REDIGIDO***'
    $texto = $texto -replace 'APP_USR-[\w-]{20,}', '***REDIGIDO***'
    $texto = $texto -replace 'TG-[\w-]{20,}', '***REDIGIDO***'
    $texto = $texto -replace 'gh[pousr]_[A-Za-z0-9]{20,}', '***REDIGIDO***'
    # So redige quando o valor PARECE segredo: texto corrido sem parenteses,
    # aspas ou $. Sem isso, `API_KEY = os.getenv("API_KEY", "")` viraria
    # `API_KEY = ***REDIGIDO***` e eu perderia justamente o codigo a analisar.
    $texto = $texto -replace '(?im)^(\s*(?:[A-Z0-9_]*(?:TOKEN|SECRET|SENHA|PASSWORD|APIKEY|API_KEY|_KEY|CHAVE|ASSINATURA))\s*[:=]\s*)([A-Za-z0-9_\-:\.]{12,})\s*$', '$1***REDIGIDO***'
    return $texto
}

# ------------------------------------------------------------- o que entra
$ExtTexto = @(".py",".ps1",".bat",".cmd",".json",".md",".yml",".yaml",".txt",
              ".toml",".cfg",".ini",".html",".css",".js",".sql",".env.example")

# Segmentos de caminho barrados. Regex aplicada ao caminho relativo.
$DirBarradas = '(^|\\)(\.git|__pycache__|node_modules|\.venv|venv|env|\.pytest_cache|dist|build|\.mypy_cache|ml_profile|fotos_cache)(\\|$)'
$NomeBarrado = '(?i)(token|cookie|session|credential|\.pem$|id_rsa)'

Write-Host ""
Write-Host "Empacotando: $Pasta" -ForegroundColor Cyan
Write-Host ""

$todos = Get-ChildItem -Path $Pasta -Recurse -File -ErrorAction SilentlyContinue
$sel = New-Object System.Collections.ArrayList
$pulados = 0

foreach ($f in $todos) {
    $rel = $f.FullName.Substring($Pasta.Length).TrimStart('\')

    if ($rel -match $DirBarradas)      { $pulados++; continue }
    if ($f.Name -match $NomeBarrado)   { $pulados++; continue }
    if ($f.Name -eq ".env")            { $pulados++; continue }
    if ($f.Name -like ".env.*" -and $f.Name -ne ".env.example") { $pulados++; continue }
    if ($f.Length -gt 1MB)             { $pulados++; continue }

    $ext = $f.Extension.ToLower()
    $entra = $ExtTexto -contains $ext
    if ($ComLogs -and ($ext -eq ".log" -or $ext -eq ".jsonl")) { $entra = $true }
    if (-not $entra) { $pulados++; continue }

    [void]$sel.Add([pscustomobject]@{ Rel = $rel; Full = $f.FullName; Len = $f.Length })
}

if ($sel.Count -eq 0) {
    Write-Host "Nenhum arquivo elegivel encontrado." -ForegroundColor Yellow
    exit 1
}

$sel = $sel | Sort-Object Rel

# ------------------------------------------------------- cabecalho + arvore
$cab = New-Object System.Collections.ArrayList
[void]$cab.Add("# Pasta $Pasta")
[void]$cab.Add("")
[void]$cab.Add("Gerado em " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " por empacotar_projeto.ps1")
[void]$cab.Add("Arquivos incluidos: $($sel.Count)   |   ignorados: $pulados")
[void]$cab.Add("Segredos: .env nao incluido; conteudo redigido. Ver cabecalho do script.")
[void]$cab.Add("")
[void]$cab.Add("## Arvore")
[void]$cab.Add('```')
foreach ($s in $sel) { [void]$cab.Add(("{0,9}  {1}" -f $s.Len, $s.Rel)) }
[void]$cab.Add('```')

# ----------------------------------------- .env: so os nomes, nunca o valor
$envPath = Join-Path $Pasta ".env"
if (Test-Path $envPath) {
    [void]$cab.Add("")
    [void]$cab.Add("## .env (apenas nomes das chaves)")
    [void]$cab.Add('```')
    foreach ($l in (Get-Content $envPath)) {
        if ($l -match '^\s*#' -or $l -notmatch '=') { continue }
        $chave = ($l -split '=', 2)[0].Trim()
        $valor = ($l -split '=', 2)[1]
        if ([string]::IsNullOrWhiteSpace($valor)) { [void]$cab.Add("$chave = (VAZIO)") }
        else { [void]$cab.Add("$chave = (preenchido, $($valor.Trim().Length) caracteres)") }
    }
    [void]$cab.Add('```')
}
[void]$cab.Add("")

# ------------------------------------------------------- escrita em partes
$limite = [int]($MaxMB * 1MB)
$parte = 1
$buf = New-Object System.Text.StringBuilder
[void]$buf.AppendLine(($cab -join "`r`n"))
$gerados = New-Object System.Collections.ArrayList
$redigidos = 0

function Gravar-Parte([System.Text.StringBuilder]$conteudo, [int]$n) {
    $caminho = Join-Path $Desktop ("{0}_{1}.md" -f $Prefixo, $n)
    Set-Content -Path $caminho -Value $conteudo.ToString() -Encoding UTF8
    return $caminho
}

foreach ($s in $sel) {
    try { $txt = Get-Content $s.Full -Raw -ErrorAction Stop } catch { continue }
    if ($null -eq $txt) { $txt = "" }
    $limpo = Redigir $txt
    if ($limpo -ne $txt) { $redigidos++ }

    $cerca = '```'
    $ext = $s.Rel -replace '.*\.', ''
    $bloco = "`r`n## $($s.Rel)`r`n$cerca$ext`r`n$limpo`r`n$cerca`r`n"

    if (($buf.Length + $bloco.Length) -gt $limite -and $buf.Length -gt 0) {
        [void]$gerados.Add((Gravar-Parte $buf $parte))
        $parte++
        $buf = New-Object System.Text.StringBuilder
        [void]$buf.AppendLine("# Pasta $Pasta - parte $parte")
        [void]$buf.AppendLine("")
    }
    [void]$buf.Append($bloco)
}
if ($buf.Length -gt 0) { [void]$gerados.Add((Gravar-Parte $buf $parte)) }

# ------------------------------------------------------------------ resumo
Write-Host "Arquivos incluidos : $($sel.Count)" -ForegroundColor Green
Write-Host "Arquivos ignorados : $pulados" -ForegroundColor DarkGray
Write-Host "Trechos redigidos  : $redigidos" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Gerado na Area de Trabalho:" -ForegroundColor Cyan
foreach ($g in $gerados) {
    $kb = [math]::Round((Get-Item $g).Length / 1KB, 0)
    Write-Host ("  {0}  ({1} KB)" -f (Split-Path $g -Leaf), $kb)
}
Write-Host ""
Write-Host "Anexe esse(s) arquivo(s) na conversa." -ForegroundColor Yellow
