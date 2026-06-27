@echo off
chcp 65001 >nul
echo.
echo ====================================================
echo    CONFIGURAR SESSAO DO PORTAL DE AFILIADOS ML
echo ====================================================
echo.
echo Este assistente vai abrir o navegador para voce
echo fazer login no Mercado Livre e ativar os links
echo oficiais de afiliado (meli.la) no bot.
echo.
echo Voce so precisa fazer isso UMA VEZ.
echo.
cd /d C:\bot_ofertas
python -m affiliates.mercadolivre setup
echo.
pause
