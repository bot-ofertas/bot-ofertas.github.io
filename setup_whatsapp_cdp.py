# -*- coding: utf-8 -*-
"""
LOGIN DO WHATSAPP DO BOT via Chrome dedicado (CDP) + visualizador ao vivo.

Conecta ao Chrome do bot (porta 9222, janela REAL — o WhatsApp aceita a
vinculação) e serve o QR em http://localhost:8723/qr.html, que se atualiza
sozinho (não expira). A sessão fica salva em data/chrome_bot.

Pré-requisito: o Chrome do bot precisa estar aberto (iniciar_whatsapp_bot.bat).
Uso: python setup_whatsapp_cdp.py
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
QR_PNG = os.path.join(DATA, "qr.png")
STATUS = os.path.join(DATA, "wa_status.txt")
PORTA = 8723
CDP = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")


def _status(msg):
    with open(STATUS, "w", encoding="utf-8") as f:
        f.write(msg)


def _servir():
    handler = partial(SimpleHTTPRequestHandler, directory=DATA)
    ThreadingHTTPServer(("127.0.0.1", PORTA), handler).serve_forever()


async def main():
    from playwright.async_api import async_playwright
    from core.chrome_manager import garantir_chrome_pronto

    _status("INICIANDO")
    threading.Thread(target=_servir, daemon=True).start()
    print("=" * 55)
    print("  ABRA NO NAVEGADOR:  http://localhost:8723/qr.html")
    print("=" * 55, flush=True)

    # Garante Chrome pronto ANTES de conectar (elimina ECONNREFUSED)
    if not garantir_chrome_pronto(timeout=60):
        _status("ERRO_CHROME")
        print("Chrome do bot não subiu — abortando setup.", flush=True)
        return

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP, timeout=15000)
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            if "web.whatsapp.com" in (pg.url or ""):
                page = pg
                break
        if page is None:
            page = await ctx.new_page()
            await page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)

        for _ in range(1200):  # ~1 hora
            if await page.query_selector('#pane-side'):
                _status("LOGADO")
                print("\n✅ LOGADO! Sessão salva em data/chrome_bot/", flush=True)
                await asyncio.sleep(3)
                return

            try:
                recarregar = await page.query_selector(
                    'div[data-ref] button, button[aria-label*="ecarregar"], '
                    'span[data-icon="refresh-large"]'
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


if __name__ == "__main__":
    asyncio.run(main())
