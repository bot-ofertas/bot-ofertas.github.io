# -*- coding: utf-8 -*-
"""
Publicação automática em grupos do WhatsApp.

Modo local (pywhatkit + WhatsApp Web):
    - Exige WhatsApp Web aberto no Chrome com sessão ativa
    - Funciona só em execução local, NÃO no GitHub Actions

Modo webhook (Evolution API / Baileys):
    - Exige servidor Evolution API rodando (Docker)
    - Funciona em qualquer ambiente
    - Configure WHATSAPP_WEBHOOK_URL + WHATSAPP_INSTANCE + WHATSAPP_API_KEY

Configuração mínima (.env):
    WHATSAPP_GROUP_ID=ABC123@g.us    ← ID do grupo (ver instruções abaixo)

Como obter o WHATSAPP_GROUP_ID:
    1. Abra https://web.whatsapp.com
    2. Clique no grupo de divulgação
    3. URL muda para: https://web.whatsapp.com/...#id=XXXXX@g.us
    4. Copie o ID após '#id=' até o fim
"""
from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse

log = logging.getLogger(__name__)

def _placeholder(valor: str) -> bool:
    """True para os valores de exemplo do `.env.example`.

    Um placeholder e uma string nao-vazia, entao passaria por configuracao
    de verdade e ligaria o envio apontando para um grupo que nao existe —
    pior que estar desligado, porque a fila consome o item e o marca como
    processado. Mesma convencao do `setup_n8n._efetivo`.
    """
    return valor.lower().startswith(("cole_aqui", "cole-aqui", "seu_", "sua_"))


def _group_id() -> str:
    valor = os.getenv("WHATSAPP_GROUP_ID", "").strip()
    return "" if _placeholder(valor) else valor


def _canal_nome() -> str:
    return os.getenv("WHATSAPP_CHANNEL_NAME", "")


def _canal_link() -> str:
    return os.getenv("WHATSAPP_CHANNEL_LINK", "")


def wa_ativo() -> bool:
    return bool(_group_id())


def marcar_link_para_whatsapp(texto: str) -> str:
    """Troca a marcação de origem (matt_source do ML / ascsubtag da Amazon)
    de "bot_telegram" pra "bot_whatsapp" em qualquer link embutido no texto.

    Sem isso, Telegram e WhatsApp publicavam literalmente a MESMA URL de
    afiliado — pedido do Daniel em 2026-08-24 pra cada plataforma ter seu
    próprio link (mais credibilidade, e dá pra medir clique por canal
    separado no próprio painel do Mercado Livre/Amazon). Troca de string no
    texto final da mensagem (não só no campo "link" do produto) pra cobrir
    também o caminho de mensagem_override da IA/fallback (core/ai_content.py
    via rastreador.py e rastreador_amazon.py), que monta o texto inteiro
    (com o link já embutido) ANTES de chegar aqui — passar só produto["link"]
    não pegaria esse caso. Troca de texto, não gera link novo do zero: evita
    duplicar a chamada lenta ao portal de afiliados (Playwright) só pra
    trocar um parâmetro de rastreamento.

    Correção de 2026-08-27: a troca por `str.replace` só funcionava quando a
    origem era exatamente "bot_telegram". Link sem `matt_source`, com outro
    valor, ou vindo do encurtador oficial passava batido e o WhatsApp
    publicava com a marcação do Telegram — a métrica por canal ficava
    errada em silêncio. Agora cada URL do texto é reescrita com
    `core.tracking.marcar_origem`, que usa `urllib.parse` (Regra 11) e
    preserva `matt_tool`/`tag` intactos (Regras 3 e 4).
    """
    if not texto:
        return texto

    import re  # noqa: PLC0415
    from core.tracking import marcar_origem  # noqa: PLC0415

    # Pontuação final de frase não faz parte da URL (o ")" fica de fora
    # também, por causa de links entre parênteses).
    padrao = re.compile(r"https?://[^\s<>\"']+")

    def _troca(m: "re.Match[str]") -> str:
        bruta = m.group(0)
        limpa = bruta.rstrip(".,;:!?)\u201d\"'")
        sufixo = bruta[len(limpa):]
        try:
            return marcar_origem(limpa, "whatsapp") + sufixo
        except Exception:
            return bruta  # nunca quebra a mensagem por causa do tracking

    return padrao.sub(_troca, texto)


def montar_mensagem_wa(produto: dict) -> str:
    titulo = produto.get("titulo") or "Oferta especial"
    preco: float | None = produto.get("preco")
    preco_original: float | None = produto.get("preco_original")
    link: str = produto.get("link") or produto.get("affiliate_link") or ""
    cupom: str | None = produto.get("cupom")
    fonte: str = produto.get("fonte") or "ml"
    categoria: str = produto.get("categoria") or ""

    desc_pct = ""
    if preco and preco_original and preco_original > preco:
        pct = int(round((1 - preco / preco_original) * 100))
        desc_pct = f"  ({pct}% OFF)"

    preco_txt = f"R$ {preco:.2f}{desc_pct}" if preco else ""
    fonte_txt = "Amazon Brasil" if fonte == "amazon" else "Mercado Livre"

    linhas = [
        "🔥 *OFERTA EXCLUSIVA!*",
        "",
        f"*{titulo}*",
        "",
    ]
    if preco_txt:
        linhas.append(f"💰 {preco_txt}")
    if cupom:
        linhas += [
            f"🏷️ *CUPOM:* `{cupom}`",
            "↳ Use na finalização da compra!",
        ]
    linhas += [
        "",
        f"🛡️ Oferta verificada · via {fonte_txt}",
        "",
        f"👉 {link}",
    ]
    if categoria:
        linhas.append(f"\n#{categoria} #oferta #desconto #publicidade")

    # CTA do canal do Telegram nas mensagens do WhatsApp (e vice-versa no
    # Telegram): cada grupo alimenta o outro em vez de os dois crescerem
    # isolados. Best-effort — problema no módulo de divulgação nunca pode
    # impedir o envio da oferta.
    try:
        from core.divulgacao import GRUPO_TELEGRAM  # noqa: PLC0415
        from core.tracking import link_utm  # noqa: PLC0415
        linhas += [
            "",
            "📢 Receba antes no Telegram: "
            + link_utm(GRUPO_TELEGRAM, origem="whatsapp",
                       campanha="grupo_ofertas", conteudo="cta_mensagem"),
        ]
    except Exception:
        pass

    return "\n".join(filter(lambda x: x is not None, linhas))


def share_url(produto: dict) -> str:
    """Retorna URL wa.me para compartilhamento manual (sem API)."""
    texto = montar_mensagem_wa(produto)
    return f"https://wa.me/?text={urllib.parse.quote(texto)}"


async def enviar_para_grupo(produto: dict, mensagem_override: str | None = None) -> bool:
    """Envia para o grupo WhatsApp configurado.

    Ordem de tentativa (mais confiável primeiro):
      1. Evolution API (se configurada) — headless, ideal para servidor.
      2. Playwright/CDP (Chrome dedicado, se WHATSAPP_CHROME_FALLBACK=1) —
         DESLIGADO por padrão desde 2026-08-04 a pedido do Daniel: mesmo
         confirmando de verdade que a foto anexou, exigia manter uma
         janela do Chrome dedicada aberta na tela (visível, mesmo que sem
         roubar o foco), e ele preferiu abrir mão dessa garantia extra em
         troca de usar só o WhatsApp Desktop nativo, que ele já tinha
         aberto e logado. Só ativa de novo se WHATSAPP_CHROME_FALLBACK=1
         for setado explicitamente.
      3. WhatsApp Desktop nativo (Windows) — MÉTODO PRINCIPAL desde
         2026-08-04. Usa app já logado, sem precisar de QR nem de janela
         extra visível, mas SEM confirmação real: testado ao vivo em
         2026-08-02 que o app roda dentro de um WebView2 (Chromium
         embutido) opaco tanto à UI Automation (árvore vira só "Pane"
         genérico) quanto a screenshot clássico (captura preta — renderização via DirectComposition não
         passa pelo BitBlt) — não dá pra confirmar que a foto anexou antes
         de enviar a legenda.
      4. pyautogui em WhatsApp Web — só se WHATSAPP_PYAUTOGUI_FALLBACK=1.
    """
    group_id = _group_id()
    if not group_id:
        return False

    mensagem = marcar_link_para_whatsapp(mensagem_override or montar_mensagem_wa(produto))
    foto_url = produto.get("foto") or produto.get("imagem") or ""
    nome_grupo = os.getenv("WHATSAPP_GROUP_NAME", "Bot-Ofertas")

    # ── Tentativa 1: Evolution API (endpoint HTTP com foto+legenda) ──────────
    # Método preferido — funciona em servidor headless e não depende do PC ligado.
    try:
        from integrations.whatsapp_api import (  # noqa: PLC0415
            enviar_oferta_completa, _configurada as _api_configurada,
        )
        if _api_configurada():
            if enviar_oferta_completa(produto, mensagem):
                return True
            log.info("WA API não enviou — caindo para WhatsApp Desktop.")
    except Exception as e:
        log.warning("WA API falhou: %s", e)

    if os.getenv("GITHUB_ACTIONS"):
        log.debug("WhatsApp local ignorado em GitHub Actions (sem display)")
        return False

    # ── Tentativa 2: Playwright/CDP — OPT-IN (WHATSAPP_CHROME_FALLBACK=1) ────
    # Preferido sobre o Desktop nativo QUANDO configurado: confirma de
    # verdade (via DOM real do Chromium) que o preview de foto abriu antes
    # de enviar — ver nota na docstring acima sobre por que o Desktop
    # nativo não consegue dar essa garantia. Requer QR scan uma vez em
    # iniciar_whatsapp_bot.bat.
    #
    # IMPORTANTE: quando essa flag está ligada, NÃO cai mais pro Desktop
    # nativo se o Playwright falhar. Testado ao vivo em 2026-08-03: isso
    # causava exatamente o bug que essa flag existe pra evitar — uma falha
    # transitória (ex: checagem de login demorando mais que o timeout) já
    # bastava pra cair de volta no caminho sem garantia nenhuma, silenciosamente,
    # 100% das rodadas. Uma falha aqui agora só significa "sem WhatsApp
    # pra esse produto desta vez" (Telegram nunca depende do WhatsApp) —
    # nunca "sem garantia pra esse produto".
    if os.getenv("WHATSAPP_CHROME_FALLBACK", "0") == "1":
        try:
            from integrations.whatsapp_playwright import enviar_whatsapp_bg  # noqa: PLC0415
            caminho = _baixar_foto(foto_url) if foto_url else ""
            ok = await enviar_whatsapp_bg(nome_grupo, mensagem, caminho)
            _limpar_fotos_antigas()
            if ok:
                return True
            log.warning("WhatsApp Playwright não enviou — pulando (sem fallback pro Desktop sem garantia).")
        except Exception as e:
            log.warning("WhatsApp Playwright falhou: %s — pulando (sem fallback pro Desktop sem garantia).", e)
        return False

    # ── Tentativa 3: WhatsApp Desktop (só Windows) — pula em Linux/VPS ──────
    # Só chega aqui se WHATSAPP_CHROME_FALLBACK não estiver configurado
    # (ninguém fez o QR scan ainda) — nesse caso é a única opção disponível.
    import sys  # noqa: PLC0415
    if sys.platform == "win32":
        try:
            from integrations.whatsapp_desktop import (  # noqa: PLC0415
                enviar_para_grupo_desktop, _processo_wa_rodando,
            )
            # Checa só o PROCESSO (não a janela) -- checar _janela_whatsapp()
            # aqui bloqueava o envio inteiro sempre que o app estava rodando
            # só minimizado na bandeja (processo de pé, sem janela
            # enumerável), que é justamente o caso que
            # enviar_para_grupo_desktop() -> garantir_whatsapp_aberto() já
            # sabe recuperar sozinho (reabre via URI whatsapp:). Com o check
            # antigo, esse código de auto-recuperação nunca era alcançado —
            # confirmado ao vivo em 2026-08-24: WhatsApp.Root.exe rodando,
            # zero janela, e a rotina inteira pulada silenciosamente por ~20
            # minutos até o restart do bot (nenhum log de wa_silencioso
            # apareceu no intervalo, só "falhou (sessão?)" repetido).
            if _processo_wa_rodando():
                # Roda em thread separada com timeout de VERDADE: pyautogui
                # é síncrono/bloqueante -- chamar direto dentro de uma
                # corrotina async trava o event loop inteiro pra sempre se
                # travar (ex: janela que nunca ganha foco), e nenhum
                # asyncio.wait_for() por fora consegue interromper (só
                # interrompe em pontos de await, código síncrono não tem
                # nenhum). Confirmado ao vivo em 2026-08-14: campanha_
                # ferramentas.py travou quase 2h sem nenhum erro registrado,
                # apesar do wait_for(timeout=90) que já existia em volta da
                # chamada inteira em campanha_ferramentas.py/rastreador.py.
                # asyncio.to_thread() move o bloqueio pra uma thread própria
                # -- wait_for() aqui consegue desistir de verdade (a thread
                # órfã continua rodando sozinha, mas não trava mais o resto).
                try:
                    ok = await asyncio.wait_for(
                        asyncio.to_thread(enviar_para_grupo_desktop, nome_grupo, mensagem, foto_url),
                        timeout=75.0,
                    )
                except asyncio.TimeoutError:
                    log.warning("WhatsApp Desktop travou por >75s — desistindo (thread órfã segue em segundo plano, sem bloquear o resto).")
                    ok = False
                await _enviar_para_canal_best_effort(enviar_para_grupo_desktop, mensagem, foto_url)
                if ok:
                    return True
                log.info("WhatsApp Desktop não enviou.")
        except Exception as e:
            log.warning("WhatsApp Desktop falhou: %s", e)

    # ── Tentativa 4: pyautogui em Web — só se explicitamente habilitado ──────
    if os.getenv("WHATSAPP_PYAUTOGUI_FALLBACK", "0") == "1":
        log.info("Usando fallback pyautogui (atrapalha a digitação).")
        return _enviar_via_pyautogui(mensagem, foto_url)

    return False


async def _enviar_para_canal_best_effort(enviar_para_grupo_desktop, mensagem: str, foto_url: str) -> None:
    """Reenvia a mesma oferta pro Canal de transmissão do WhatsApp
    (WHATSAPP_CHANNEL_LINK), se configurado.

    Best-effort e isolado do envio do grupo: nunca conta pro retorno de
    enviar_para_grupo() (evitaria dobrar posts_whatsapp_total por engano
    quando só o canal funciona) e uma falha aqui nunca derruba o envio do
    grupo, que já rodou antes desta chamada.

    Usa link direto (whatsapp://), não busca por nome (Ctrl+F) -- achado
    ao vivo em 2026-08-11: a busca do WhatsApp Desktop não alcança a aba
    de Canais/Atualizações (separada de Conversas/Grupos), então a foto+
    legenda caía na última conversa aberta em vez do canal, sem erro
    nenhum reportado. Abrir pelo link de convite (protocolo whatsapp://,
    registrado no Windows por HKCR\\whatsapp) abre a conversa certa direto,
    sem busca e sem passar por navegador algum (que mostraria um diálogo
    de confirmação e travaria a automação desatendida)."""
    link_canal = _canal_link()
    if not link_canal:
        return
    try:
        # Mesma proteção de thread+timeout do envio ao grupo -- ver
        # comentário em enviar_para_grupo() sobre por que isso é essencial
        # com pyautogui (código síncrono, sem pontos de await internos).
        ok = await asyncio.wait_for(
            asyncio.to_thread(enviar_para_grupo_desktop, "Canal de ofertas", mensagem, foto_url, link_canal),
            timeout=75.0,
        )
        if ok:
            log.info("✅ WA: também enviado pro canal (via link)")
        else:
            log.info("Canal WhatsApp não enviou.")
    except asyncio.TimeoutError:
        log.warning("Envio pro canal WhatsApp travou por >75s — desistindo (thread órfã segue em segundo plano).")
    except Exception as e:
        log.warning("Envio pro canal WhatsApp falhou: %s", e)


def _baixar_foto(foto_url: str) -> str:
    """Baixa a foto do produto e salva como JPG otimizado em data/. Retorna o caminho ou ''.

    Imagem reduzida a 800px e qualidade 80 — sobe rápido no WhatsApp e
    carrega rápido para quem recebe, sem perder nitidez no chat.
    """
    if not foto_url or not foto_url.startswith("http"):
        return ""
    try:
        import io    # noqa: PLC0415
        import time  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        # `requests.get()` cru manda o User-Agent padrao da biblioteca, e o
        # `mlstatic` responde 403 a ele — a causa 2 do "nao esta aparecendo
        # as fotos". A correcao ja existia para o Telegram (core/foto_url),
        # mas o WhatsApp seguia baixando do jeito antigo: mesma oferta, foto
        # num canal e sem foto no outro. `baixar_melhor` manda cabecalho de
        # navegador e ainda tenta a variante 1x quando a original sumiu do
        # CDN. Regra 5: sem foto o envio nao sai, entao a diferenca aqui e
        # entre publicar no grupo e nao publicar.
        from core.foto_url import baixar_melhor  # noqa: PLC0415

        conteudo, _ = baixar_melhor(foto_url)
        if not conteudo:
            return ""

        img = Image.open(io.BytesIO(conteudo)).convert("RGB")
        img.thumbnail((800, 800))

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # Nome único evita conflito se dois envios ocorrerem em sequência
        destino = os.path.join(base, "data", f"wa_foto_{int(time.time() * 1000)}.jpg")
        img.save(destino, "JPEG", quality=80, optimize=True)
        return destino
    except Exception as e:
        log.warning("Falha ao baixar foto: %s", e)
        return ""


def _limpar_fotos_antigas() -> None:
    """Remove fotos temporárias do WhatsApp com mais de alguns minutos."""
    try:
        import glob  # noqa: PLC0415
        import time  # noqa: PLC0415
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        agora = time.time()
        for f in glob.glob(os.path.join(base, "data", "wa_foto_*.jpg")):
            try:
                if agora - os.path.getmtime(f) > 300:  # 5 min
                    os.remove(f)
            except OSError:
                pass
    except Exception:
        pass


def _copiar_arquivo_clipboard(caminho: str) -> bool:
    """Copia um ARQUIVO para a área de transferência (CF_HDROP), como no Explorer.

    Ao colar (Ctrl+V) no WhatsApp Web, o arquivo é anexado de verdade — o
    navegador faz upload dos bytes reais, sem corrupção de imagem.
    """
    if not caminho or not os.path.exists(caminho):
        return False
    try:
        import struct  # noqa: PLC0415
        import win32clipboard  # noqa: PLC0415
        import win32con  # noqa: PLC0415

        # DROPFILES: pFiles(offset), pt.x, pt.y, fNC, fWide  = 20 bytes
        offset = 20
        lista = (caminho + "\0\0").encode("utf-16-le")  # dupla terminação nula
        dropfiles = struct.pack("<LllII", offset, 0, 0, 0, 1)  # fWide=1 (unicode)
        buf = dropfiles + lista

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, buf)
        finally:
            # Sem isso, uma exceção entre Open/Set deixa o clipboard do
            # Windows travado pro processo inteiro (copiar/colar do próprio
            # usuário para de funcionar, e envios seguintes falham em cascata).
            win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        log.warning("Falha ao copiar arquivo para clipboard: %s", e)
        return False


def _enviar_via_pyautogui(mensagem: str, foto_url: str = "") -> bool:
    """Envia via automação do Chrome com WhatsApp Web, com foto do produto.

    Garante que a aba do WhatsApp Web está ativa antes de enviar, mesmo que o
    Chrome esteja em outra aba. Se houver foto, cola a imagem (preview) e usa a
    mensagem como legenda; caso contrário envia só texto.
    """
    import threading  # noqa: PLC0415
    import time       # noqa: PLC0415
    try:
        import pygetwindow as gw  # noqa: PLC0415
        import pyautogui          # noqa: PLC0415
        import pyperclip          # noqa: PLC0415
    except ImportError:
        log.warning("Falta pyautogui/pygetwindow/pyperclip — instale: pip install pyautogui pygetwindow pyperclip")
        return False

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.3

    # ── Baixa a foto EM PARALELO enquanto navega a janela (economiza tempo) ──
    _foto = {"caminho": ""}
    if foto_url:
        def _baixar():
            _foto["caminho"] = _baixar_foto(foto_url)
        t_foto = threading.Thread(target=_baixar, daemon=True)
        t_foto.start()
    else:
        t_foto = None

    # Encontra janela Chrome com aba WhatsApp ativa OU qualquer janela Chrome
    janelas_wa = gw.getWindowsWithTitle("WhatsApp")
    janelas_chrome = [w for w in gw.getAllWindows()
                      if "chrome" in w.title.lower() or "google" in w.title.lower()]

    janela = (janelas_wa or janelas_chrome or [None])[0]
    if not janela:
        log.warning("Chrome não encontrado. Abra o Chrome com https://web.whatsapp.com")
        return False

    def _trazer_para_frente(win):
        """Ativa a janela tolerando o falso-erro 'Error code 0' do pygetwindow."""
        for _ in (1, 2):
            try:
                win.activate()
                return
            except Exception:
                # pygetwindow lança "Error code 0 (sucesso)" mesmo quando funciona;
                # tenta restaurar/maximizar como alternativa e segue em frente
                try:
                    if win.isMinimized:
                        win.restore()
                    win.maximize()
                except Exception:
                    pass
                time.sleep(0.3)

    try:
        _trazer_para_frente(janela)
        time.sleep(0.8)

        # Se a aba ativa não é WhatsApp, navega para web.whatsapp.com na barra de endereço
        if not janelas_wa:
            log.info("Aba WhatsApp não ativa — navegando para web.whatsapp.com...")
            pyautogui.hotkey("ctrl", "l")   # foca barra de endereço
            time.sleep(0.4)
            pyperclip.copy("https://web.whatsapp.com")
            pyautogui.hotkey("ctrl", "v")
            pyautogui.press("enter")
            time.sleep(6)                   # aguarda WhatsApp Web carregar

        # Abre o grupo Bot-Ofertas via atalho de busca
        pyautogui.hotkey("ctrl", "alt", "/")
        time.sleep(0.6)

        pyautogui.typewrite("Bot-Ofertas", interval=0.05)
        time.sleep(1.0)

        pyautogui.press("down")
        time.sleep(0.2)
        pyautogui.press("enter")
        time.sleep(1.2)

        # ── Com foto: cola o ARQUIVO → preview faz upload → legenda → enviar ──
        if t_foto:
            t_foto.join(timeout=12)         # garante que a foto terminou de baixar
        caminho_foto = _foto["caminho"]
        if caminho_foto and _copiar_arquivo_clipboard(caminho_foto):
            pyautogui.hotkey("ctrl", "v")   # cola o arquivo (WhatsApp faz upload real)
            time.sleep(3.0)                 # aguarda upload + preview (imagem 800px sobe rápido)
            # A caixa de legenda já vem focada; cola o texto como legenda
            pyperclip.copy(mensagem)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.6)
            pyautogui.press("enter")        # envia imagem + legenda
            time.sleep(1.0)
            _limpar_fotos_antigas()
            log.info("✅ WhatsApp enviado COM FOTO (arquivo) para grupo %s", _group_id())
            return True

        log.warning("Sem foto disponível — envio abortado (nunca posta incompleto)")
        return False
    except Exception as e:
        log.warning("pyautogui falhou: %s", e)
        return False
