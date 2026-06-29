@echo off
chcp 65001 > nul
title Bot Ofertas - Sistema Completo
color 0A

echo ========================================
echo   BOT OFERTAS - SISTEMA COMPLETO
echo ========================================
echo.
echo 1. Verificando WhatsApp Web no Chrome...
echo    Acesse: https://web.whatsapp.com
echo    Abra o grupo "Bot-Ofertas" antes de continuar.
echo.
echo Pressione qualquer tecla quando o WhatsApp Web estiver aberto...
pause > nul

echo.
echo 2. Iniciando rastreador de ofertas (Telegram + WhatsApp)...
echo    Postagem a cada 20 minutos automaticamente.
echo.
cd /d "C:\bot_ofertas"
python rastreador.py --loop 20

pause
