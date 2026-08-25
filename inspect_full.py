# -*- coding: utf-8 -*-
"""Anexa uma foto e dumpa TODOS os elementos do preview para achar a caixa de legenda."""
import sys, asyncio, os, json, glob
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main():
    from playwright.async_api import async_playwright
    fotos = sorted(glob.glob("data/wa_foto_*.jpg"), key=os.path.getmtime)
    if not fotos:
        from integrations.whatsapp_sender import _baixar_foto
        foto = _baixar_foto("https://http2.mlstatic.com/D_NQ_NP_2X_658777-MLB54400850847_032023-F.webp")
    else:
        foto = fotos[-1]
    foto = os.path.abspath(foto)
    print("foto:", foto)

    p = await async_playwright().start()
    b = await p.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=15000)
    ctx = b.contexts[0]
    page = None
    for pg in ctx.pages:
        if "web.whatsapp.com" in (pg.url or ""):
            page = pg; break
    if not page:
        print("SEM aba WhatsApp aberta"); return
    await asyncio.sleep(1)

    # Fecha dialog residual
    dlg = await page.query_selector('div[role="dialog"][aria-modal="true"]')
    if dlg:
        for btn in await dlg.query_selector_all('button'):
            t = (await btn.inner_text()).strip().lower()
            if 'descartar' in t:
                await btn.click(); break
        await asyncio.sleep(1)

    # Abre grupo
    busca = await page.wait_for_selector('[role="textbox"][data-tab="3"], [aria-label*="Pesquisar"]', timeout=10000)
    await busca.click()
    await page.keyboard.press("Control+A"); await page.keyboard.press("Delete")
    await busca.type("Bot-Ofertas", delay=20)
    await asyncio.sleep(2); await page.keyboard.press("Enter"); await asyncio.sleep(2)

    # Anexa foto
    fi = await page.wait_for_selector('input[type="file"][accept*="image"]', timeout=6000, state="attached")
    await fi.set_input_files(foto)
    await asyncio.sleep(5)  # aguarda preview

    # Dump COMPLETO do que existe agora
    info = await page.evaluate("""() => {
        function desc(el, i) {
            const rect = el.getBoundingClientRect();
            return {
                idx: i,
                tag: el.tagName,
                role: el.getAttribute('role'),
                ariaLabel: el.getAttribute('aria-label'),
                dataTab: el.getAttribute('data-tab'),
                placeholder: el.getAttribute('data-placeholder') || el.getAttribute('placeholder'),
                contenteditable: el.getAttribute('contenteditable'),
                visible: rect.width > 0 && rect.height > 0,
                pos: `${Math.round(rect.left)},${Math.round(rect.top)} ${Math.round(rect.width)}x${Math.round(rect.height)}`,
                text: (el.innerText || '').slice(0, 50),
            };
        }
        const out = { textboxes: [], contenteditable: [], legendaLike: [] };
        [...document.querySelectorAll('[role="textbox"]')].forEach((el, i) => out.textboxes.push(desc(el, i)));
        [...document.querySelectorAll('[contenteditable="true"]')].forEach((el, i) => out.contenteditable.push(desc(el, i)));
        [...document.querySelectorAll('[aria-label]')].forEach((el, i) => {
            const a = el.getAttribute('aria-label') || '';
            if (/legenda|caption|adicione/i.test(a)) out.legendaLike.push(desc(el, i));
        });
        return out;
    }""")
    print(json.dumps(info, indent=2, ensure_ascii=False))
    await b.close(); await p.stop()


asyncio.run(main())
