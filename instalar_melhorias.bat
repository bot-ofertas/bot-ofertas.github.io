@echo off
chcp 65001 >nul
cd /d C:\bot_ofertas
echo [%date% %time%] Instalando dependencias...
pip install -r requirements.txt --quiet
echo [%date% %time%] Dependencias instaladas!
echo.
echo Para usar AI rewriting, abra o arquivo .env e preencha:
echo   ANTHROPIC_API_KEY=sua_chave_do_console.anthropic.com
echo.
pause
