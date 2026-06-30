# -*- coding: utf-8 -*-
"""
LOGIN DO WHATSAPP DO BOT (QR ao vivo via servidor local)
=========================================================
Captura o QR Code do WhatsApp Web (headless) a cada 3s e o serve em
http://localhost:8723/qr.html — o código se atualiza sozinho, então NÃO expira
enquanto você prepara o celular.

Como usar:
    1. python setup_whatsapp_qr.py
    2. Abra http://localhost:8723/qr.html no navegador
    3. Celular: WhatsApp > Aparelhos conectados > Conectar, e escaneie
    4. Quando aparecer "Conectado", a sessão está salva. Pode fechar.

A sessão fica em data/wa_profile/ e é usada pelo rastreador (envio em segundo plano).
"""
import asyncio
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
PERFIL = os.path.join(DATA, "wa_profile")
QR_PNG = os.path.join(DATA, "qr.png")
STATUS = os.path.join(DATA, "wa_status.txt")
PORTA = 8723

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def _status(msg):
    with open(STATUS, "w", encoding="utf-8") as f:
        f.write(msg)


def _servir():
    """Sobe um servidor HTTP simples servindo a pasta data/ (para o qr.html)."""
    handler = partial(SimpleHTTPRequestHandler, directory=DATA)
    httpd = ThreadingHTTPServer(("127.0.0.1", PORTA), handler)
    httpd.serve_forever()


async def main():
    from playwright.async_api import async_playwright

    os.makedirs(PERFIL, exist_ok=True)
    _status("INICIANDO")

    # Servidor local em thread (para o visualizador qr.html)
    threading.Thread(target=_servir, daemon=True).start()
    print("=" * 55)
    print("  ABRA NO NAVEGADOR:  http://localhost:8723/qr.html")
    print("=" * 55, flush=True)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PERFIL, headless=True, user_agent=_UA, locale="pt-BR",
            viewport={"width": 1280, "height": 900},
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)

        for _ in range(1200):  # ~1 hora capturando a cada 3s
            if await page.query_selector('#pane-side'):
                _status("LOGADO")
                print("\n✅ LOGADO! Sessão salva em data/wa_profile/", flush=True)
                await asyncio.sleep(3)
                await ctx.close()
                return

            # Se o QR expirou, o WhatsApp mostra "recarregar" — clica p/ gerar novo
            try:
                recarregar = await page.query_selector(
                    'div[data-ref] button, button[aria-label*="ecarregar"], '
                    'span[data-icon="refresh-large"], div[role="button"][tabindex]'
                )
                if recarregar:
                    await recarregar.click()
                    await asyncio.sleep(2)
            except Exception:
                pass

            try:
                qr = await page.query_selector('canvas[aria-label], div[data-ref]')
                if qr:
                    await qr.screenshot(path=QR_PNG)
                    _status("QR")
            except Exception:
                pass
            await asyncio.sleep(3)

        _status("TIMEOUT")
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
