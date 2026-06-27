@echo off
chcp 65001 >nul
cd /d C:\bot_ofertas

echo [%date% %time%] Iniciando Bot Ofertas ML...

:: Mata processos anteriores
taskkill /f /im python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: Inicia rastreador em loop (a cada 60 minutos)
start /min "BotOfertas-Loop" python rastreador.py --loop 60

:: Inicia dashboard web
start /min "BotOfertas-Dashboard" python web\app.py

echo [%date% %time%] Bot iniciado! Dashboard em http://localhost:5000
