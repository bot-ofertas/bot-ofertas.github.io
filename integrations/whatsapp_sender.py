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


def _copiar_imagem_clipboard(foto_url: str) -> bool:
    """Baixa a foto do produto e copia para a área de transferência do Windows.

    Retorna True se a imagem foi copiada com sucesso (pronta para Ctrl+V).
    """
    if not foto_url or not foto_url.startswith("http"):
        return False
    try:
        import io  # noqa: PLC0415
        import requests  # noqa: PLC0415
        import win32clipboard  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        r = requests.get(foto_url, timeout=12)
        if r.status_code != 200 or not r.content:
            return False

        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        # Limita tamanho para evitar imagens gigantes
        img.thumbnail((1280, 1280))

        # Converte para BMP/DIB (formato exigido pelo clipboard do Windows)
        out = io.BytesIO()
        img.save(out, "BMP")
        dib = out.getvalue()[14:]  # remove o cabeçalho BMP de 14 bytes -> DIB
        out.close()

        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
        win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        log.warning("Falha ao copiar imagem para clipboard: %s", e)
        return False


def _enviar_via_pyautogui(mensagem: str, foto_url: str = "") -> bool:
    """Envia via automação do Chrome com WhatsApp Web, com foto do produto.

    Garante que a aba do WhatsApp Web está ativa antes de enviar, mesmo que o
    Chrome esteja em outra aba. Se houver foto, cola a imagem (preview) e usa a
    mensagem como legenda; caso contrário envia só texto.
    """
    import time  # noqa: PLC0415
    try:
        import pygetwindow as gw  # noqa: PLC0415
        import pyautogui          # noqa: PLC0415
        import pyperclip          # noqa: PLC0415
    except ImportError:
        log.warning("Falta pyautogui/pygetwindow/pyperclip — instale: pip install pyautogui pygetwindow pyperclip")
        return False

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.4

    # Encontra janela Chrome com aba WhatsApp ativa OU qualquer janela Chrome
    janelas_wa = gw.getWindowsWithTitle("WhatsApp")
    janelas_chrome = [w for w in gw.getAllWindows()
                      if "chrome" in w.title.lower() or "google" in w.title.lower()]

    janela = (janelas_wa or janelas_chrome or [None])[0]
    if not janela:
        log.warning("Chrome não encontrado. Abra o Chrome com https://web.whatsapp.com")
        return False

    try:
        janela.activate()
        time.sleep(1.0)

        # Se a aba ativa não é WhatsApp, navega para web.whatsapp.com na barra de endereço
        if not janelas_wa:
            log.info("Aba WhatsApp não ativa — navegando para web.whatsapp.com...")
            pyautogui.hotkey("ctrl", "l")   # foca barra de endereço
            time.sleep(0.5)
            pyperclip.copy("https://web.whatsapp.com")
            pyautogui.hotkey("ctrl", "v")
            pyautogui.press("enter")
            time.sleep(6)                   # aguarda WhatsApp Web carregar

        # Abre o grupo Bot-Ofertas via atalho de busca
        pyautogui.hotkey("ctrl", "alt", "/")
        time.sleep(0.8)

        pyautogui.typewrite("Bot-Ofertas", interval=0.07)
        time.sleep(1.5)

        pyautogui.press("down")
        time.sleep(0.3)
        pyautogui.press("enter")
        time.sleep(2.0)

        # ── Com foto: cola imagem → abre preview → legenda → enviar ───────────
        if foto_url and _copiar_imagem_clipboard(foto_url):
            pyautogui.hotkey("ctrl", "v")   # cola a imagem (abre tela de preview)
            time.sleep(2.5)                 # aguarda preview da imagem abrir
            # A caixa de legenda já vem focada; cola o texto como legenda
            pyperclip.copy(mensagem)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.6)
            pyautogui.press("enter")        # envia imagem + legenda
            time.sleep(1.0)
            log.info("✅ WhatsApp enviado COM FOTO via pyautogui para grupo %s", _group_id())
            return True

        # ── Sem foto: só texto ───────────────────────────────────────────────
        pyperclip.copy(mensagem)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)
        pyautogui.press("enter")
        time.sleep(0.5)

        log.info("✅ WhatsApp enviado (texto) via pyautogui para grupo %s", _group_id())
        return True
    except Exception as e:
        log.warning("pyautogui falhou: %s", e)
        return False
