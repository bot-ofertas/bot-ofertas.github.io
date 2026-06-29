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

import logging
import os
import urllib.parse

log = logging.getLogger(__name__)

def _group_id() -> str:
    return os.getenv("WHATSAPP_GROUP_ID", "")


def wa_ativo() -> bool:
    return bool(_group_id())


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

    return "\n".join(filter(lambda x: x is not None, linhas))


def share_url(produto: dict) -> str:
    """Retorna URL wa.me para compartilhamento manual (sem API)."""
    texto = montar_mensagem_wa(produto)
    return f"https://wa.me/?text={urllib.parse.quote(texto)}"


async def enviar_para_grupo(produto: dict, mensagem_override: str | None = None) -> bool:
    """Tenta enviar para o grupo WhatsApp configurado.

    Usa mensagem_override se fornecida (conteúdo gerado por IA), caso contrário
    monta a mensagem padrão. Tenta Evolution API primeiro, cai para pyautogui.
    """
    group_id = _group_id()
    if not group_id:
        return False

    mensagem = mensagem_override or montar_mensagem_wa(produto)

    webhook_url = os.getenv("WHATSAPP_WEBHOOK_URL", "")
    wa_api_key  = os.getenv("WHATSAPP_API_KEY", "")
    wa_instance = os.getenv("WHATSAPP_INSTANCE", "default")

    # ── Tentativa 1: Evolution API (headless, funciona em server) ─────────────
    if webhook_url and wa_api_key:
        try:
            import requests  # noqa: PLC0415
            resp = requests.post(
                f"{webhook_url}/message/sendText/{wa_instance}",
                headers={"apikey": wa_api_key, "Content-Type": "application/json"},
                json={"number": group_id, "textMessage": {"text": mensagem}},
                timeout=10,
            )
            if resp.status_code in (200, 201):
                log.info("WhatsApp enviado via Evolution API para %s", group_id)
                return True
            log.warning("Evolution API erro %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("Evolution API falhou: %s", e)

    # ── Tentativa 2: pyautogui (requer WhatsApp Web aberto no Chrome) ────────
    if os.getenv("GITHUB_ACTIONS"):
        log.debug("pyautogui ignorado em GitHub Actions (sem display)")
        return False
    foto_url = produto.get("foto") or produto.get("imagem") or ""
    return _enviar_via_pyautogui(mensagem, foto_url)


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
        import requests  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        r = requests.get(foto_url, timeout=10)
        if r.status_code != 200 or not r.content:
            return ""

        img = Image.open(io.BytesIO(r.content)).convert("RGB")
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
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_HDROP, buf)
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

        # ── Sem foto: só texto ───────────────────────────────────────────────
        pyperclip.copy(mensagem)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)
        pyautogui.press("enter")
        time.sleep(0.5)

        log.info("✅ WhatsApp enviado (texto) para grupo %s", _group_id())
        return True
    except Exception as e:
        log.warning("pyautogui falhou: %s", e)
        return False
