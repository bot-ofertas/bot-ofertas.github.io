@echo off
chcp 65001 > nul
title Chrome do Bot - WhatsApp
echo Abrindo o Chrome dedicado do bot (WhatsApp Web)...
echo Escaneie o QR uma vez. Depois pode minimizar esta janela.
echo.
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
start "" "%CHROME%" --user-data-dir="C:\bot_ofertas\data\chrome_bot" --remote-debugging-port=9222 --no-first-run --no-default-browser-check https://web.whatsapp.com
