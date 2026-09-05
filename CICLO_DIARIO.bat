@echo off
REM Duplo clique aqui: registra o ciclo diario (liga 08:30 / desliga 02:00).
REM Chama o PowerShell com -ExecutionPolicy Bypass porque a politica padrao do
REM Windows recusa .ps1 baixado, e o erro que ela mostra nao explica isso.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configurar_ciclo.ps1" %*
echo.
pause
