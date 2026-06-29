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

_GROUP_ID       = os.getenv("WHATSAPP_GROUP_ID", "")
_WEBHOOK_URL    = os.getenv("WHATSAPP_WEBHOOK_URL", "")     # ex: http://localhost:8080
_WA_INSTANCE    = os.getenv("WHATSAPP_INSTANCE", "default") # nome da instância Evolution
_WA_API_KEY     = os.getenv("WHATSAPP_API_KEY", "")

_ATIVO = bool(_GROUP_ID)


def wa_ativo() -> bool:
    return _ATIVO


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
    monta a mensagem padrão. Tenta Evolution API primeiro, cai para pywhatkit.
    """
    if not _ATIVO:
        return False

    mensagem = mensagem_override or montar_mensagem_wa(produto)

    # ── Tentativa 1: Evolution API (headless, funciona em server) ─────────────
    if _WEBHOOK_URL and _WA_API_KEY:
        try:
            import requests  # noqa: PLC0415
            resp = requests.post(
                f"{_WEBHOOK_URL}/message/sendText/{_WA_INSTANCE}",
                headers={"apikey": _WA_API_KEY, "Content-Type": "application/json"},
                json={"number": _GROUP_ID, "textMessage": {"text": mensagem}},
                timeout=10,
            )
            if resp.status_code in (200, 201):
                log.info("WhatsApp enviado via Evolution API para %s", _GROUP_ID)
                return True
            log.warning("Evolution API erro %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("Evolution API falhou: %s", e)

    # ── Tentativa 2: pyautogui (requer WhatsApp Web aberto no Chrome) ────────
    if os.getenv("GITHUB_ACTIONS"):
        log.debug("pyautogui ignorado em GitHub Actions (sem display)")
        return False
    return _enviar_via_pyautogui(mensagem)


def _enviar_via_pyautogui(mensagem: str) -> bool:
    """Envia via automação do Chrome com WhatsApp Web aberto na guia do grupo Bot-Ofertas."""
    import time  # noqa: PLC0415
    try:
        import pygetwindow as gw  # noqa: PLC0415
        import pyautogui          # noqa: PLC0415
        import pyperclip          # noqa: PLC0415
    except ImportError:
        log.warning("Falta pyautogui/pygetwindow/pyperclip — instale: pip install pyautogui pygetwindow pyperclip")
        return False

    janelas = gw.getWindowsWithTitle("WhatsApp")
    if not janelas:
        log.warning("WhatsApp Web não encontrado. Abra o Chrome com https://web.whatsapp.com e o grupo 'Bot-Ofertas' aberto.")
        return False

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.4

    try:
        janelas[0].activate()
        time.sleep(1.5)

        pyautogui.hotkey("ctrl", "alt", "/")
        time.sleep(0.8)

        pyautogui.typewrite("Bot-Ofertas", interval=0.07)
        time.sleep(1.5)

        pyautogui.press("down")
        time.sleep(0.3)
        pyautogui.press("enter")
        time.sleep(2.0)

        pyperclip.copy(mensagem)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)
        pyautogui.press("enter")
        time.sleep(0.5)

        log.info("✅ WhatsApp enviado via pyautogui para grupo %s", _GROUP_ID)
        return True
    except Exception as e:
        log.warning("pyautogui falhou: %s", e)
        return False
