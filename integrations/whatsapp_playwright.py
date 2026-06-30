# -*- coding: utf-8 -*-
"""
ENVIO WHATSAPP EM SEGUNDO PLANO (Playwright headless, sessão própria)
======================================================================
O bot mantém a PRÓPRIA sessão do WhatsApp Web num navegador headless (invisível),
com perfil persistente. Envia as ofertas manipulando o DOM — NÃO usa mouse/teclado
físicos, NÃO abre janela e NÃO rouba o foco. Você usa o PC normalmente.

Login: uma única vez via QR (rode: python setup_whatsapp_qr.py e abra data/qr.html).
A sessão fica salva em data/wa_profile/ e persiste entre execuções.

Uso (dentro do rastreador, async):
    from integrations.whatsapp_playwright import enviar_whatsapp_bg, fechar_whatsapp
    ok = await enviar_whatsapp_bg(grupo, mensagem, caminho_foto)
    ...
    await fechar_whatsapp()   # ao fim da rodada
"""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PERFIL = os.path.join(_BASE, "data", "wa_profile")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# Singleton mantido vivo durante uma rodada (mesmo event loop)
_pw = None
_ctx = None
_page = None


async def _abrir_contexto():
    """Abre (ou reaproveita) o navegador headless com WhatsApp Web carregado."""
    global _pw, _ctx, _page

    if _page is not None and not _page.is_closed():
        return _page

    from playwright.async_api import async_playwright  # noqa: PLC0415

    os.makedirs(_PERFIL, exist_ok=True)
    _pw = await async_playwright().start()
    _ctx = await _pw.chromium.launch_persistent_context(
        _PERFIL, headless=True, user_agent=_UA, locale="pt-BR",
        viewport={"width": 1280, "height": 900},
        args=["--no-first-run", "--no-default-browser-check",
              "--disable-blink-features=AutomationControlled"],
    )
    _page = _ctx.pages[0] if _ctx.pages else await _ctx.new_page()
    if "web.whatsapp.com" not in (_page.url or ""):
        await _page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)
    return _page


async def _esta_logado(page) -> bool:
    try:
        await page.wait_for_selector(
            '#pane-side, div[aria-label="Lista de conversas"]', timeout=15000
        )
        return True
    except Exception:
        return False


async def _abrir_grupo(page, nome_grupo: str) -> bool:
    try:
        busca_sel = (
            'div[contenteditable="true"][data-tab="3"], '
            'div[role="textbox"][data-tab="3"], '
            'div[title="Caixa de texto de pesquisa"]'
        )
        busca = await page.wait_for_selector(busca_sel, timeout=10000)
        await busca.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        await busca.type(nome_grupo, delay=15)
        await asyncio.sleep(1.5)
        await page.keyboard.press("Enter")
        await asyncio.sleep(1.2)
        return True
    except Exception as e:
        log.warning("Não consegui abrir o grupo '%s': %s", nome_grupo, e)
        return False


async def _enviar_texto(page, mensagem: str) -> bool:
    try:
        compose_sel = (
            'footer div[contenteditable="true"][data-tab="10"], '
            'footer div[contenteditable="true"], '
            'div[contenteditable="true"][data-tab="10"]'
        )
        caixa = await page.wait_for_selector(compose_sel, timeout=10000)
        await caixa.click()
        await page.keyboard.insert_text(mensagem)
        await asyncio.sleep(0.4)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.6)
        return True
    except Exception as e:
        log.warning("Falha ao enviar texto: %s", e)
        return False


async def _enviar_foto(page, caminho_foto: str, legenda: str) -> bool:
    try:
        input_sel = 'input[type="file"][accept*="image"]'
        try:
            file_input = await page.wait_for_selector(input_sel, timeout=4000, state="attached")
        except Exception:
            anexar = await page.wait_for_selector(
                'div[title="Anexar"], span[data-icon="plus"], span[data-icon="clip"], '
                'span[data-icon="plus-rounded"], button[title="Anexar"]',
                timeout=5000,
            )
            await anexar.click()
            await asyncio.sleep(0.6)
            file_input = await page.wait_for_selector(input_sel, timeout=5000, state="attached")

        await file_input.set_input_files(caminho_foto)
        await asyncio.sleep(2.5)

        legenda_sel = (
            'div[contenteditable="true"][data-tab="1"], '
            'div[contenteditable="true"][aria-label*="legenda"], '
            'div[contenteditable="true"][data-tab="10"]'
        )
        try:
            cap = await page.wait_for_selector(legenda_sel, timeout=6000)
            await cap.click()
            await page.keyboard.insert_text(legenda)
            await asyncio.sleep(0.4)
        except Exception:
            log.info("Caixa de legenda não encontrada — foto irá sem legenda.")

        try:
            enviar = await page.wait_for_selector(
                'span[data-icon="send"], div[role="button"][aria-label="Enviar"]', timeout=5000
            )
            await enviar.click()
        except Exception:
            await page.keyboard.press("Enter")
        await asyncio.sleep(1.0)
        return True
    except Exception as e:
        log.warning("Falha ao enviar foto: %s", e)
        return False


async def enviar_whatsapp_bg(nome_grupo: str, mensagem: str, caminho_foto: str = "") -> bool:
    """Envia uma oferta ao grupo em segundo plano (headless, sem mexer no PC).

    Retorna True se enviou. Se a sessão não estiver logada, retorna False e avisa
    para o usuário escanear o QR (python setup_whatsapp_qr.py + data/qr.html).
    """
    try:
        page = await _abrir_contexto()
    except Exception as e:
        log.warning("Não foi possível abrir o navegador headless do WhatsApp: %s", e)
        return False

    if not await _esta_logado(page):
        log.warning("WhatsApp do bot NÃO está logado. Rode 'python setup_whatsapp_qr.py', "
                    "abra data/qr.html e escaneie o QR uma vez.")
        return False

    if not await _abrir_grupo(page, nome_grupo):
        return False

    if caminho_foto and os.path.exists(caminho_foto):
        if await _enviar_foto(page, caminho_foto, mensagem):
            log.info("✅ WhatsApp (bg headless) enviado COM FOTO para '%s'", nome_grupo)
            return True
        log.info("Foto falhou — enviando como texto.")

    if await _enviar_texto(page, mensagem):
        log.info("✅ WhatsApp (bg headless) enviado (texto) para '%s'", nome_grupo)
        return True
    return False


async def fechar_whatsapp() -> None:
    """Fecha o navegador headless ao fim da rodada (libera o event loop)."""
    global _pw, _ctx, _page
    try:
        if _ctx is not None:
            await _ctx.close()
    except Exception:
        pass
    try:
        if _pw is not None:
            await _pw.stop()
    except Exception:
        pass
    _pw = _ctx = _page = None
