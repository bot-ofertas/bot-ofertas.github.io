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
import re

log = logging.getLogger(__name__)

_CDP_URL = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222")


def _normalizar_espacos(texto: str) -> str:
    """Colapsa qualquer sequência de espaços/quebras de linha em um só
    espaço. innerText de um contenteditable não preserva a mesma contagem
    de \\n do texto colado (WhatsApp Web renderiza linha em branco como
    <div><br></div>, que vira \\n\\n\\n em vez de \\n\\n) — comparar direto
    dava falso-positivo de "legenda não bate" mesmo com o conteúdo
    idêntico. Ainda pega diferença de CONTEÚDO de verdade (texto de outra
    oferta misturado, por exemplo), só ignora formatação de espaço."""
    return re.sub(r"\s+", " ", texto).strip()

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
    """Confirma que a sessão está logada checando a lista de conversas.

    Testado ao vivo em 2026-08-03: essa checagem estava reportando "não
    logado" em 100% das rodadas reais (sempre caindo pro WhatsApp Desktop
    nativo, sem garantia), mesmo com a sessão genuinamente ativa — um
    teste manual direto contra a mesma página, no mesmo momento, achou os
    dois seletores instantaneamente. A causa exata (throttling de aba em
    segundo plano, um estado transitório de reconexão do WhatsApp Web, ou
    algo na própria checagem) não ficou 100% clara porque a exceção real
    era engolida em silêncio — por isso agora loga o motivo de verdade e
    tenta 2x com folga maior antes de desistir, em vez de decidir "não
    logado" no primeiro tropeço."""
    for tentativa in (1, 2):
        try:
            await page.wait_for_selector(
                '#pane-side, div[aria-label="Lista de conversas"]', timeout=20000
            )
            return True
        except Exception as e:
            log.warning("Checagem de login (tentativa %d/2) falhou: %s", tentativa, e)
            if tentativa == 1:
                await asyncio.sleep(2)
    return False


# Seletores atuais do WhatsApp Web (jun/2026):
#   busca: <input role=textbox data-tab=3>   compose: <div contenteditable data-tab=10> no footer
_BUSCA_SEL = ('[role="textbox"][data-tab="3"], [aria-label*="Pesquisar"], '
              'div[contenteditable="true"][data-tab="3"]')
_COMPOSE_SEL = ('footer [contenteditable="true"][data-tab="10"], '
                'footer div[contenteditable="true"]')


async def _limpar_dialogos_residuais(page) -> None:
    """Fecha qualquer diálogo de confirmação que tenha ficado aberto de uma
    tentativa anterior (ex: "Deseja descartar a seleção?", que aparece
    quando um preview de foto/legenda é abandonado no meio). Achado ao
    vivo em 2026-08-03: um diálogo desses ficou preso bloqueando TODA
    interação futura com a página (o clique na caixa de busca esperava os
    30s inteiros porque o diálogo interceptava os eventos de ponteiro) —
    até alguém notar e fechar manualmente. Chamado no início de cada envio
    pra a sessão se auto-recuperar sozinha, sem depender de intervenção
    manual."""
    try:
        descartar = page.get_by_role("button", name="Descartar")
        if await descartar.count() > 0:
            log.warning("Diálogo residual encontrado ('Deseja descartar a seleção?') — fechando")
            await descartar.click(timeout=5000)
            await asyncio.sleep(0.5)
    except Exception as e:
        log.info("Limpeza de diálogo residual: %s", e)


async def _abrir_grupo(page, nome_grupo: str) -> bool:
    """Abre a conversa do grupo pela busca.

    Usa page.locator() em vez de wait_for_selector()+ElementHandle
    guardado — Locator resolve o elemento de novo a cada ação, então uma
    re-renderização do WhatsApp Web entre "achar" e "clicar" (ex: um
    badge de não-lida atualizando, ou o app re-montando parte da barra
    lateral) não deixa a referência "stale". Achado ao vivo em
    2026-08-03: ElementHandle.click travava os 30s completos esperando um
    elemento que já não existia mais no DOM. Tenta 2x antes de desistir.
    """
    busca = page.locator(_BUSCA_SEL).first
    for tentativa in (1, 2):
        try:
            await busca.click(timeout=10000)
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            await page.keyboard.type(nome_grupo, delay=15)
            await asyncio.sleep(1.5)
            await page.keyboard.press("Enter")
            await asyncio.sleep(1.3)
            # Limpa o campo de busca para não deixar texto residual
            try:
                await busca.click(timeout=5000)
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Delete")
            except Exception:
                pass
            return True
        except Exception as e:
            log.warning("Abrir grupo '%s' (tentativa %d/2) falhou: %s", nome_grupo, tentativa, e)
            if tentativa == 1:
                await asyncio.sleep(2)
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
      4) a própria caixa de compose do rodapé (footer, data-tab=10) —
         confirmado ao vivo em 2026-08-03: a versão atual do WhatsApp Web
         reaproveita essa MESMA caixa como legenda quando a foto é anexada
         via input file (não abre nenhum contenteditable dedicado fora do
         footer nesse fluxo) — é por isso que a estratégia 2 sempre falhava
         aqui, retornando None (0 elementos fora do footer) mesmo com o
         preview genuinamente aberto e funcionando.
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
        const tb = document.querySelector('[role="textbox"][data-tab="1"]');
        if (tb) return tb;

        // 4) fallback: caixa de compose do rodapé reaproveitada como legenda
        return document.querySelector('footer [contenteditable="true"][data-tab="10"]');
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
            log.warning("Caixa de legenda NÃO encontrada — abortando (nunca posta sem descrição)")
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return False

        try:
            # .focus() via JS em vez de .click() — confirmado ao vivo em
            # 2026-08-03: um clique normal (mesmo com force=True, mesmo via
            # coordenada de mouse crua) fica bloqueado por uma camada que
            # intercepta o ponteiro nessa caixa específica quando ela é a
            # do rodapé reaproveitada (ver _achar_legenda estratégia 4);
            # .focus() direto no elemento funciona de forma confiável e foi
            # testado digitando e conferindo o texto de volta.
            await caixa_legenda.evaluate("el => el.focus()")
            await asyncio.sleep(0.3)

            # LIMPA a caixa antes de colar — achado ao vivo em 2026-08-03,
            # com evidência real: sem isso, a caixa (que é a MESMA caixa de
            # compose do rodapé reaproveitada, não uma nova a cada preview)
            # ACUMULA o texto de tentativas anteriores. Um teste real
            # colou 1790 caracteres de 5 produtos diferentes concatenados
            # em vez dos 392 esperados de 1 só — se o WhatsApp corta a
            # legenda em algum limite de caracteres, é exatamente isso que
            # fazia o link (e o resto) da oferta atual sumir: o conteúdo
            # de verdade ficava enterrado atrás de lixo acumulado.
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            await asyncio.sleep(0.2)

            # Cola em vez de digitar (mais rápido e preserva quebras de linha)
            await page.evaluate("nav => navigator.clipboard.writeText(nav)", legenda)
            await page.keyboard.press("Control+V")
            await asyncio.sleep(0.4)
            # Verifica se o texto colado bate com o esperado — não só "não
            # vazio", mas EXATO. Sem essa comparação, a corrupção por
            # acúmulo (achado ao vivo) passava despercebida: a caixa tinha
            # texto (não vazio), só que era o texto errado (de rodadas
            # anteriores misturado).
            texto = await caixa_legenda.inner_text()
            if _normalizar_espacos(texto) != _normalizar_espacos(legenda):
                log.warning("Legenda colada não bate com a esperada (%d vs %d chars) — limpando e tentando de novo",
                            len(texto), len(legenda))
                # Fallback: limpa de novo e digita via insert_text
                await caixa_legenda.evaluate("el => el.focus()")
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Delete")
                await asyncio.sleep(0.2)
                await page.keyboard.insert_text(legenda)
                await asyncio.sleep(0.4)
                texto = await caixa_legenda.inner_text()
        except Exception as e:
            log.warning("Falha ao digitar legenda: %s — abortando (nunca posta sem descrição)", e)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return False

        if _normalizar_espacos(texto) != _normalizar_espacos(legenda):
            log.warning("Legenda ainda não bate com a esperada após nova tentativa (%d vs %d chars) — "
                        "abortando (nunca posta incompleto/incorreto)", len(texto), len(legenda))
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return False

        log.info("Legenda digitada (%d chars): %r", len(texto), texto[:50])

        # Clica o botão de enviar correto (nunca Enter — evita virar figurinha)
        enviar = await page.wait_for_selector(_SEND_PREVIEW_SEL, timeout=6000)
        await enviar.click()
        await asyncio.sleep(1.5)
        return True
    except Exception as e:
        log.warning("Falha ao enviar foto: %s", e)
        return False


_MUTEX_NOME = "Global\\BotOfertas_WhatsAppPlaywright_Lock"


def _adquirir_lock(timeout_s: float = 40.0):
    """Trava exclusiva entre processos. ML/Amazon/Ferramentas rodam como
    processos separados e TODOS compartilham a MESMA aba do Chrome via CDP
    (_conectar() reaproveita a aba existente do WhatsApp Web) — sem essa
    trava, dois processos clicando/digitando na mesma página ao mesmo
    tempo se atropelavam, raiz real do bug de fotos chegando sem legenda
    mesmo com o código de digitação correto. Mesmo padrão de
    integrations/whatsapp_desktop_silencioso.py (_adquirir_lock_whatsapp),
    nome de mutex diferente pra não competir com aquela trava à toa."""
    try:
        import win32event  # noqa: PLC0415
        handle = win32event.CreateMutex(None, False, _MUTEX_NOME)
        resultado = win32event.WaitForSingleObject(handle, int(timeout_s * 1000))
        if resultado in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
            return handle
        try:
            import win32api  # noqa: PLC0415
            win32api.CloseHandle(handle)
        except Exception:
            pass
        return None
    except Exception as e:
        log.warning("Trava do WhatsApp/Playwright indisponível (pywin32?): %s", e)
        return None


def _liberar_lock(handle) -> None:
    if handle is None:
        return
    try:
        import win32event  # noqa: PLC0415
        import win32api  # noqa: PLC0415
        win32event.ReleaseMutex(handle)
        win32api.CloseHandle(handle)
    except Exception:
        pass


async def enviar_whatsapp_bg(nome_grupo: str, mensagem: str, caminho_foto: str = "") -> bool:
    """Envia uma oferta ao grupo em segundo plano via CDP (sem mexer no PC).

    Retorna True se enviou. Se não conectar ao Chrome do bot (porta 9222), ou se
    a sessão não estiver logada, retorna False e registra um aviso.

    Serializa entre processos (ML/Amazon/Ferramentas) via mutex nomeado —
    ver _adquirir_lock.
    """
    lock = _adquirir_lock()
    if lock is None:
        log.warning("Não consegui a trava do WhatsApp/Playwright (outro processo usando há >40s) — pulando envio")
        return False
    try:
        return await _enviar_whatsapp_bg_impl(nome_grupo, mensagem, caminho_foto)
    finally:
        _liberar_lock(lock)


async def _enviar_whatsapp_bg_impl(nome_grupo: str, mensagem: str, caminho_foto: str = "") -> bool:
    try:
        page = await asyncio.wait_for(_conectar(), timeout=30)
    except Exception as e:
        log.warning("Não conectei ao Chrome do bot (%s). Ele precisa estar aberto "
                    "(iniciar_whatsapp_bot.bat). Erro: %s", _CDP_URL, e)
        return False

    if not await _esta_logado(page):
        log.warning("WhatsApp do bot NÃO está logado. Abra a janela do Chrome do bot e escaneie o QR.")
        return False

    await _limpar_dialogos_residuais(page)

    if not await _abrir_grupo(page, nome_grupo):
        return False

    if not caminho_foto or not os.path.exists(caminho_foto):
        log.warning("Sem foto disponível — envio abortado (nunca posta incompleto)")
        return False

    if await _enviar_foto(page, caminho_foto, mensagem):
        log.info("✅ WhatsApp (bg/CDP) enviado COM FOTO para '%s'", nome_grupo)
        return True

    log.warning("Foto falhou ao enviar — envio abortado (nunca posta incompleto)")
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
