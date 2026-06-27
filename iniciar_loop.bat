@echo off
chcp 65001 >nul
cd /d C:\bot_ofertas
:loop
echo [%date% %time%] Iniciando rodada...
python rastreador.py >> data\rastreador_auto.log 2>&1
echo [%date% %time%] Rodada concluida. Aguardando 60 minutos...
timeout /t 3600 /nobreak >nul
goto loop
