@echo off
REM corrigir_tudo.bat -- clique duplo, ou chame daqui.
REM
REM Existe por causa da ExecutionPolicy do Windows: `.\corrigir_tudo.ps1`
REM morre com "a execucao de scripts foi desabilitada neste sistema"
REM (PSSecurityException). Aconteceu com o Daniel em 20/09/2026, no ultimo
REM passo de um resgate que ja tinha dado certo em tudo o mais.
REM
REM -ExecutionPolicy Bypass vale SO para esta chamada: nao altera a politica
REM da maquina (Regra 10 -- nao mexer em configuracao de seguranca do PC).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0corrigir_tudo.ps1" %*
echo.
echo Pressione qualquer tecla para fechar.
pause >nul
