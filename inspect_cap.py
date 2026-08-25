# -*- coding: utf-8 -*-
import sys, asyncio, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main():
    from playwright.async_api import async_playwright
    from integrations.whatsapp_sender import _baixar_foto
    foto = _baixar_foto("https://http2.mlstatic.com/D_NQ_NP_2X_658777-MLB54400850847_032023-F.webp")
    foto = os.path.abspath(foto)

    p = await async_playwright().start()
    b = await p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=15000)
    ctx = b.contexts[0]
    page = [pg for pg in ctx.pages if "web.whatsapp.com" in (pg.url or "")][0]
    await asyncio.sleep(1)
    # fecha dialog se houver
    dlg = await page.query_selector('div[role="dialog"][aria-modal="true"]')
    if dlg:
        for btn in await dlg.query_selector_all('button'):
            t = (await btn.inner_text()).strip().lower()
            if 'descartar' in t:
                await btn.click(); break
        await asyncio.sleep(1)

    busca = await page.wait_for_selector('[role="textbox"][data-tab="3"], [aria-label*="Pesquisar"]', timeout=10000)
    await busca.click()
    await page.keyboard.press("Control+A"); await page.keyboard.press("Delete")
    await busca.type("Bot-Ofertas", delay=20)
    await asyncio.sleep(2); await page.keyboard.press("Enter"); await asyncio.sleep(2)

    fi = await page.wait_for_selector('input[type="file"][accept*="image"]', timeout=6000, state="attached")
    await fi.set_input_files(foto)
    await asyncio.sleep(4)

    caps = await page.evaluate("""() => {
        return [...document.querySelectorAll('[contenteditable="true"]')].map(e => ({
            tab: e.getAttribute('data-tab'),
            aria: e.getAttribute('aria-label'),
            role: e.getAttribute('role'),
            placeholder: e.getAttribute('data-placeholder'),
            inFooter: !!e.closest('footer'),
        }));
    }""")
    import json
    print("CONTENTEDITABLE no preview de foto:")
    print(json.dumps(caps, indent=2, ensure_ascii=False))
    await b.close(); await p.stop()


asyncio.run(main())
