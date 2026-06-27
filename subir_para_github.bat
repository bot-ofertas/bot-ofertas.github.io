@echo off
chcp 65001 >nul
echo.
echo ===============================================================
echo  BOT OFERTAS ML — Subir para GitHub (rodar sem PC ligado)
echo ===============================================================
echo.
echo Este script vai subir o bot para o GitHub para rodar 24h/dia.
echo.
echo ANTES DE CONTINUAR, você precisa:
echo  1. Ter uma conta no GitHub (github.com — é grátis)
echo  2. Ter o Git instalado (já está instalado neste PC)
echo.
pause

:: Verifica se já tem remote
git remote -v 2>nul | findstr "origin" >nul
if %errorlevel%==0 (
    echo.
    echo [OK] Remote GitHub já configurado:
    git remote -v
    goto :subir
)

echo.
echo PASSO 1: Criar repositório no GitHub
echo ─────────────────────────────────────
echo  1. Acesse: https://github.com/new
echo  2. Nome do repositório: bot-ofertas-ml
echo  3. Deixe PRIVADO (Private)
echo  4. NÃO marque "Add README" nem nada
echo  5. Clique em "Create repository"
echo.
echo Após criar, copie a URL do repositório (formato:)
echo  https://github.com/SEU_USUARIO/bot-ofertas-ml.git
echo.
set /p REPO_URL="Cole a URL aqui e pressione ENTER: "

if "%REPO_URL%"=="" (
    echo ERRO: URL não informada.
    pause
    exit /b 1
)

git remote add origin %REPO_URL%
echo [OK] Remote configurado!

:subir
echo.
echo PASSO 2: Subindo código para o GitHub...
echo ─────────────────────────────────────────
git add -A
git commit -m "feat: bot ofertas ML com GitHub Actions" 2>nul || echo (sem mudancas novas)
git branch -M main
git push -u origin main

if %errorlevel% neq 0 (
    echo.
    echo ERRO ao fazer push. Possível causa: autenticação.
    echo.
    echo Solução:
    echo  1. Acesse: https://github.com/settings/tokens/new
    echo  2. Marque "repo" e clique em "Generate token"
    echo  3. Copie o token e use como SENHA quando o Git pedir
    echo.
    pause
    exit /b 1
)

echo.
echo ✅ Código enviado!
echo.
echo ═══════════════════════════════════════════════════════════════
echo  PASSO 3: Configurar Secrets (credenciais do bot no GitHub)
echo ═══════════════════════════════════════════════════════════════
echo.
echo Acesse: https://github.com/SEU_USUARIO/bot-ofertas-ml/settings/secrets/actions
echo (substitua SEU_USUARIO pelo seu usuário do GitHub)
echo.
echo Clique em "New repository secret" e adicione:
echo.
echo  Nome: TOKEN_TELEGRAM
echo  Valor: (seu token do BotFather)
echo.
echo  Nome: CANAL_GERAL
echo  Valor: (ID do seu canal, ex: -1004260974517)
echo.
echo  Nome: ML_AFFILIATE_TOOL_ID
echo  Valor: 47114387
echo.
echo Após adicionar os 3 secrets, o bot vai rodar automaticamente
echo a cada hora das 07h às 23h (horário de Brasília).
echo.
echo Para rodar agora manualmente:
echo  1. Acesse: https://github.com/SEU_USUARIO/bot-ofertas-ml/actions
echo  2. Clique em "Bot Ofertas ML"
echo  3. Clique em "Run workflow"
echo.
pause
