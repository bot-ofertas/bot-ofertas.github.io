# -*- coding: utf-8 -*-
"""
LOGIN ÚNICO DO WHATSAPP WEB (QR Code) PARA O BOT
=================================================
Abre o navegador do bot (perfil próprio) com o WhatsApp Web visível.
Escaneie o QR Code com o celular (WhatsApp > Aparelhos conectados).

Depois disso, a sessão fica salva em data/wa_profile/ e o rastreador
passa a enviar as ofertas em SEGUNDO PLANO, sem atrapalhar o uso do PC.

Rode uma única vez:
    python setup_whatsapp.py
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
PERFIL = os.path.join(BASE, "data", "wa_profile")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


async def main():
    from playwright.async_api import async_playwright

    os.makedirs(PERFIL, exist_ok=True)
    print("=" * 55)
    print("LOGIN WHATSAPP WEB — BOT OFERTAS")
    print("=" * 55)
    print("1. Uma janela do WhatsApp Web vai abrir.")
    print("2. No celular: WhatsApp > Aparelhos conectados > Conectar.")
    print("3. Escaneie o QR Code que aparecer.")
    print("4. Quando suas conversas carregarem, a sessão está salva.")
    print("   Esta janela fecha sozinha após o login.\n")

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            PERFIL,
            headless=False,
            user_agent=_UA,
            locale="pt-BR",
            viewport={"width": 1280, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

        print("⏳ Aguardando login (até 3 minutos)...")
        logado = False
        for _ in range(90):  # 90 x 2s = 180s
            try:
                el = await page.query_selector('#pane-side, div[aria-label="Lista de conversas"]')
                if el:
                    logado = True
                    break
            except Exception:
                pass
            await asyncio.sleep(2)

        if logado:
            print("\n✅ LOGIN OK! Sessão salva em data/wa_profile/")
            print("   O rastreador agora envia em segundo plano. Pode fechar tudo.")
            await asyncio.sleep(3)
        else:
            print("\n⚠️  Não detectei o login no tempo limite. Rode novamente e escaneie o QR.")

        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
