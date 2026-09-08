@echo off
REM Duplo-clique: instala/atualiza tudo (bot + n8n + ciclo diario).
REM Existe porque o PowerShell recusa script nao assinado por padrao, e o
REM -ExecutionPolicy Bypass daqui vale so para esta execucao — nao muda
REM configuracao nenhuma da maquina.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar_tudo.ps1" %*
echo.
pause
