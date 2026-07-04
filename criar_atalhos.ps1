# criar_atalhos.ps1 — cria atalhos na Área de Trabalho
$BASE = $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")

$sh = New-Object -ComObject WScript.Shell

# 1. Atalho para a pasta do projeto
$lnk = $sh.CreateShortcut("$desktop\Bot Ofertas.lnk")
$lnk.TargetPath = $BASE
$lnk.WorkingDirectory = $BASE
$lnk.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,3"  # ícone de pasta
$lnk.Description = "Pasta do Bot Ofertas (Telegram + WhatsApp automático)"
$lnk.Save()
Write-Host "Atalho criado: $desktop\Bot Ofertas.lnk" -ForegroundColor Green

# 2. Cria o bloco de notas vazio se não existe
$txt = Join-Path $desktop "Problemas de execução para corrigir.txt"
if (-not (Test-Path $txt)) {
    @"
============================================================================
  PROBLEMAS DE EXECUÇÃO PARA CORRIGIR — Bot Ofertas
============================================================================

Este arquivo é atualizado automaticamente pelo bot quando ocorrem erros.
Cada erro é gravado em bloco separado com:
  - Quando aconteceu
  - Qual operação falhou
  - Arquivo, função e linha exata
  - Mensagem do erro
  - Traceback

Também é possível consultar via HTTP para automação (n8n):
  http://127.0.0.1:8724/errors?limit=50

Se este arquivo estiver vazio, nenhum erro aconteceu ainda. 🎉

"@ | Set-Content -Path $txt -Encoding UTF8
    Write-Host "Bloco de notas criado: $txt" -ForegroundColor Green
}
else {
    Write-Host "Bloco de notas já existe: $txt" -ForegroundColor Yellow
}

# 3. Atalho para iniciar o bot (start.ps1)
$lnkStart = $sh.CreateShortcut("$desktop\Iniciar Bot Ofertas.lnk")
$lnkStart.TargetPath = "powershell.exe"
$lnkStart.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$BASE\start.ps1`""
$lnkStart.WorkingDirectory = $BASE
$lnkStart.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,137"
$lnkStart.Description = "Inicia o bot em segundo plano"
$lnkStart.Save()
Write-Host "Atalho criado: $desktop\Iniciar Bot Ofertas.lnk" -ForegroundColor Green

# 4. Atalho para ver status
$lnkStatus = $sh.CreateShortcut("$desktop\Status Bot Ofertas.lnk")
$lnkStatus.TargetPath = "powershell.exe"
$lnkStatus.Arguments = "-NoProfile -NoExit -ExecutionPolicy Bypass -File `"$BASE\status.ps1`""
$lnkStatus.WorkingDirectory = $BASE
$lnkStatus.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,167"
$lnkStatus.Description = "Mostra status do bot"
$lnkStatus.Save()
Write-Host "Atalho criado: $desktop\Status Bot Ofertas.lnk" -ForegroundColor Green

Write-Host "`nPronto — 4 atalhos criados na Área de Trabalho." -ForegroundColor Cyan
