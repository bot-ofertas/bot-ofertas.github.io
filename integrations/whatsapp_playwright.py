# -*- coding: utf-8 -*-
"""
ENVIO WHATSAPP EM SEGUNDO PLANO (Chrome dedicado + CDP)
=======================================================
O bot tem um Chrome PRÓPRIO (perfil data/chrome_bot, porta de depuração 9222),
separado do navegador do usuário. Esse Chrome fica logado no WhatsApp Web (sessão
própria = aparelho vinculado independente). O envio é feito manipulando o DOM via
CDP (Chrome DevTools Protocol) — NÃO usa mouse/teclado físicos nem rouba o foco.
Você usa o PC normalmente enquanto as ofertas são enviadas, igual ao Telegram.

Por que Chrome dedicado e não o seu Chrome principal?
  - Chrome 136+ bloqueia --remote-debugging-port no perfil padrão (segurança).
    Num perfil SEPARADO a porta funciona normalmente.
  - O WhatsApp não permite a mesma sessão em dois navegadores; o bot precisa do
    próprio aparelho vinculado.
  - Janela REAL (não headless): o WhatsApp aceita o login por QR (o headless é
    rejeitado na vinculação).

Login (uma vez): abra a janela do Chrome do bot (iniciar_whatsapp_bot.bat ou
startup.py) e escaneie o QR nativo do WhatsApp. A sessão fica salva em data/chrome_bot.

Uso (rastreador, async):
    from integrations.whatsapp_playwright import enviar_whatsapp_bg, fechar_whatsapp
    ok = await enviar_whatsapp_bg(grupo, mensagem, caminho_foto)
    await fechar_whatsapp()   # ao fim da rodada (apenas desconecta)
"""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger(__name__)

_CDP_URL = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")

_pw = None
_browser = None
_page = None


async def _conectar():
    """Conecta ao Chrome do bot via CDP e localiza (ou abre) a aba do WhatsApp Web.

    Sequência resiliente:
      1) Chama chrome_manager.garantir_chrome_pronto() — inicia Chrome se cair
         e espera a porta 9222 responder de fato antes de conectar.
      2) Só então tenta connect_over_cdp — impede ECONNREFUSED.
      3) Reaproveita aba existente do WhatsApp; abre nova só se não existir.
    """
    global _pw, _browser, _page

    if _page is not None and not _page.is_closed():
        return _page

    # 1) Garante Chrome operante ANTES de conectar (elimina ECONNREFUSED)
    from core.chrome_manager import garantir_chrome_pronto  # noqa: PLC0415
    if not garantir_chrome_pronto(timeout=45):
        raise RuntimeError("Chrome do bot não subiu na porta 9222 dentro do timeout")

    from playwright.async_api import async_playwright  # noqa: PLC0415

    _pw = await async_playwright().start()
    _browser = await _pw.chromium.connect_over_cdp(_CDP_URL, timeout=15000)
    ctx = _browser.contexts[0] if _browser.contexts else await _browser.new_context()

    alvo = None
    for pg in ctx.pages:
        try:
            if "web.whatsapp.com" in (pg.url or ""):
                alvo = pg
                break
        except Exception:
            continue
    if alvo is None:
        alvo = await ctx.new_page()
        await alvo.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)

    _page = alvo
    return _page


async def _esta_logado(page) -> bool:
    try:
        await page.wait_for_selector(
            '#pane-side, div[aria-label="Lista de conversas"]', timeout=12000
        )
        return True
    except Exception:
        return False


# Seletores atuais do WhatsApp Web (jun/2026):
#   busca: <input role=textbox data-tab=3>   compose: <div contenteditable data-tab=10> no footer
_BUSCA_SEL = ('[role="textbox"][data-tab="3"], [aria-label*="Pesquisar"], '
              'div[contenteditable="true"][data-tab="3"]')
_COMPOSE_SEL = ('footer [contenteditable="true"][data-tab="10"], '
                'footer div[contenteditable="true"]')


async def _abrir_grupo(page, nome_grupo: str) -> bool:
    try:
        busca = await page.wait_for_selector(_BUSCA_SEL, timeout=10000)
        await busca.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        await busca.type(nome_grupo, delay=15)
        await asyncio.sleep(1.5)
        await page.keyboard.press("Enter")
        await asyncio.sleep(1.3)
        # Limpa o campo de busca para não deixar texto residual
        try:
            await busca.click()
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
        except Exception:
            pass
        return True
    except Exception as e:
        log.warning("Não consegui abrir o grupo '%s': %s", nome_grupo, e)
        return False


async def _limpar_compose(page):
    """Esvazia a caixa de mensagem (remove rascunho) antes de digitar/enviar."""
    try:
        caixa = await page.wait_for_selector(_COMPOSE_SEL, timeout=8000)
        await caixa.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        await asyncio.sleep(0.2)
        return caixa
    except Exception:
        return None


async def _enviar_texto(page, mensagem: str) -> bool:
    try:
        caixa = await _limpar_compose(page)
        if caixa is None:
            caixa = await page.wait_for_selector(_COMPOSE_SEL, timeout=10000)
            await caixa.click()
        await page.keyboard.insert_text(mensagem)
        await asyncio.sleep(0.4)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.6)
        return True
    except Exception as e:
        log.warning("Falha ao enviar texto: %s", e)
        return False


# Botão "enviar" do preview de mídia: data-icon wds-ic-send-filled,
# aria-label "Enviar N item(ns) selecionado(s)". NÃO usar Enter (cai no sticker).
_SEND_PREVIEW_SEL = ('[data-icon="wds-ic-send-filled"], '
                     'div[role="button"][aria-label^="Enviar"], '
                     'button[aria-label^="Enviar"]')


async def _achar_legenda(page):
    """Localiza a caixa de legenda do preview de mídia com heurística robusta.

    O WhatsApp muda esses seletores com frequência. Estratégias, em ordem:
      1) contenteditable com aria-label falando de legenda/caption
      2) qualquer contenteditable VISÍVEL que NÃO esteja no footer da conversa
         (a caixa da conversa está no footer; a de legenda fica no dialog de preview)
      3) role=textbox com data-tab=1 (compatibilidade com versões antigas)
    Retorna o ElementHandle ou None.
    """
    handle = await page.evaluate_handle("""() => {
        // 1) match por aria-label
        const byAria = [...document.querySelectorAll('[contenteditable="true"][aria-label]')]
            .find(e => {
                const a = (e.getAttribute('aria-label') || '').toLowerCase();
                return a.includes('legenda') || a.includes('caption') || a.includes('adicione');
            });
        if (byAria) return byAria;

        // 2) contenteditable visível FORA do footer (o footer é da conversa)
        const eds = [...document.querySelectorAll('[contenteditable="true"]')].filter(e => {
            const r = e.getBoundingClientRect();
            if (r.width < 40 || r.height < 20) return false;
            if (e.closest('footer')) return false;
            return true;
        });
        if (eds.length) return eds[0];

        // 3) textbox data-tab=1
        return document.querySelector('[role="textbox"][data-tab="1"]');
    }""")
    if handle is None:
        return None
    # Converte JSHandle em ElementHandle
    try:
        el = handle.as_element()
        return el
    except Exception:
        return None


async def _enviar_foto(page, caminho_foto: str, legenda: str) -> bool:
    try:
        file_input = await page.wait_for_selector(
            'input[type="file"][accept*="image"]', timeout=6000, state="attached"
        )
        await file_input.set_input_files(caminho_foto)

        # Aguarda o preview montar — confirmado pela presença do botão "Enviar"
        await page.wait_for_selector(_SEND_PREVIEW_SEL, timeout=15000)
        await asyncio.sleep(1.5)  # tempo para animação e caixa de legenda montar

        # Localiza a caixa de legenda (heurística robusta) e digita
        caixa_legenda = None
        for tentativa in range(3):
            caixa_legenda = await _achar_legenda(page)
            if caixa_legenda:
                break
            await asyncio.sleep(0.8)

        if caixa_legenda is None:
            log.warning("Caixa de legenda NÃO encontrada — foto irá sem descrição.")
        else:
            try:
                await caixa_legenda.click()
                await asyncio.sleep(0.3)
                # Cola em vez de digitar (mais rápido e preserva quebras de linha)
                await page.evaluate("nav => navigator.clipboard.writeText(nav)", legenda)
                await page.keyboard.press("Control+V")
                await asyncio.sleep(0.4)
                # Verifica se digitou
                texto = await caixa_legenda.inner_text()
                if not texto.strip():
                    # Fallback: insert_text
                    await caixa_legenda.click()
                    await page.keyboard.insert_text(legenda)
                    await asyncio.sleep(0.4)
                    texto = await caixa_legenda.inner_text()
                log.info("Legenda digitada (%d chars): %r", len(texto), texto[:50])
            except Exception as e:
                log.warning("Falha ao digitar legenda: %s", e)

        # Clica o botão de enviar correto (nunca Enter — evita virar figurinha)
        enviar = await page.wait_for_selector(_SEND_PREVIEW_SEL, timeout=6000)
        await enviar.click()
        await asyncio.sleep(1.5)
        return True
    except Exception as e:
        log.warning("Falha ao enviar foto: %s", e)
        return False


async def enviar_whatsapp_bg(nome_grupo: str, mensagem: str, caminho_foto: str = "") -> bool:
    """Envia uma oferta ao grupo em segundo plano via CDP (sem mexer no PC).

    Retorna True se enviou. Se não conectar ao Chrome do bot (porta 9222), ou se
    a sessão não estiver logada, retorna False e registra um aviso.
    """
    try:
        page = await asyncio.wait_for(_conectar(), timeout=30)
    except Exception as e:
        log.warning("Não conectei ao Chrome do bot (%s). Ele precisa estar aberto "
                    "(iniciar_whatsapp_bot.bat). Erro: %s", _CDP_URL, e)
        return False

    if not await _esta_logado(page):
        log.warning("WhatsApp do bot NÃO está logado. Abra a janela do Chrome do bot e escaneie o QR.")
        return False

    if not await _abrir_grupo(page, nome_grupo):
        return False

    if caminho_foto and os.path.exists(caminho_foto):
        if await _enviar_foto(page, caminho_foto, mensagem):
            log.info("✅ WhatsApp (bg/CDP) enviado COM FOTO para '%s'", nome_grupo)
            return True
        log.info("Foto falhou — enviando como texto.")

    if await _enviar_texto(page, mensagem):
        log.info("✅ WhatsApp (bg/CDP) enviado (texto) para '%s'", nome_grupo)
        return True
    return False


async def fechar_whatsapp() -> None:
    """Apenas desconecta do Chrome do bot (NÃO fecha a janela) ao fim da rodada."""
    global _pw, _browser, _page
    try:
        if _browser is not None:
            await _browser.close()  # encerra só a conexão CDP
    except Exception:
        pass
    try:
        if _pw is not None:
            await _pw.stop()
    except Exception:
        pass
    _pw = _browser = _page = None
