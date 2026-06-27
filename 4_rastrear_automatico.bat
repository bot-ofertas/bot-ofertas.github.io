@echo off
cd /d "%~dp0"
echo Iniciando rastreador de ofertas (a cada 60 minutos)...
echo Pressione Ctrl+C para parar.
echo.
python rastreador.py --loop 60
pause
