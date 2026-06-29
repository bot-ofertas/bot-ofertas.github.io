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


async def enviar_para_grupo(produto: dict) -> bool:
    """Tenta enviar para o grupo WhatsApp configurado.

    Tenta Evolution API webhook primeiro (headless),
    cai para pywhatkit como fallback (requer WhatsApp Web aberto).
    """
    if not _ATIVO:
        return False

    mensagem = montar_mensagem_wa(produto)

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

    # ── Tentativa 2: pywhatkit (requer WhatsApp Web aberto localmente) ────────
    # Pula automaticamente em GitHub Actions (sem display nem sessão WhatsApp Web)
    if os.getenv("GITHUB_ACTIONS"):
        log.debug("pywhatkit ignorado em GitHub Actions (sem display/WhatsApp Web)")
        return False
    try:
        import pywhatkit  # noqa: PLC0415
        pywhatkit.sendwhatmsg_to_group_instantly(
            group_id=_GROUP_ID,
            message=mensagem,
            wait_time=10,
            tab_close=True,
            close_time=3,
        )
        log.info("WhatsApp enviado via pywhatkit para grupo %s", _GROUP_ID)
        return True
    except ImportError:
        log.debug("pywhatkit não instalado — instale com: pip install pywhatkit")
    except Exception as e:
        log.warning("pywhatkit falhou: %s", e)

    return False
