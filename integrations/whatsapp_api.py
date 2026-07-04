# -*- coding: utf-8 -*-
"""
WHATSAPP API — Evolution API (não-oficial, suporta GRUPOS)
===========================================================
A WhatsApp Cloud API oficial da Meta NÃO permite enviar para grupos —
apenas 1:1. Para postar em grupos automaticamente, usamos a Evolution API
(open-source, roda em Docker).

Setup:
    docker compose -f docker/evolution.yml up -d
    # Acessa http://localhost:8080, cria instância "botofertas"
    # Escaneia QR pelo celular (uma vez), pega apikey no .env

Variáveis .env:
    WHATSAPP_WEBHOOK_URL   = http://localhost:8080
    WHATSAPP_API_KEY       = sua_apikey_da_instancia
    WHATSAPP_INSTANCE      = botofertas
    WHATSAPP_GROUP_ID      = 120363XXXXXXXXX@g.us  (JID do grupo)

Endpoints Evolution v2 usados:
    POST /message/sendText/{instance}       → texto puro
    POST /message/sendMedia/{instance}      → foto + legenda
    GET  /instance/connectionState/{instance} → verifica se está logado
    GET  /group/fetchAllGroups/{instance}   → lista grupos + JIDs

Suporte a n8n:
  - Erros vão para data/errors.jsonl
  - Estado da conexão em GET /health
"""
from __future__ import annotations

import base64
import io
import logging
import os
from typing import Optional

log = logging.getLogger("whatsapp_api")


def _config() -> dict:
    return {
        "url": (os.getenv("WHATSAPP_WEBHOOK_URL", "") or "").rstrip("/"),
        "key": os.getenv("WHATSAPP_API_KEY", ""),
        "instance": os.getenv("WHATSAPP_INSTANCE", "botofertas"),
        "group_id": os.getenv("WHATSAPP_GROUP_ID", ""),
    }


def _configurada() -> bool:
    c = _config()
    return bool(c["url"] and c["key"] and c["group_id"])


def esta_conectada() -> bool:
    """Retorna True se a instância Evolution está logada no WhatsApp."""
    c = _config()
    if not (c["url"] and c["key"]):
        return False
    try:
        import requests  # noqa: PLC0415
        r = requests.get(
            f"{c['url']}/instance/connectionState/{c['instance']}",
            headers={"apikey": c["key"]},
            timeout=6,
        )
        if r.status_code != 200:
            return False
        estado = r.json().get("instance", {}).get("state", "")
        return estado == "open"
    except Exception:
        return False


def _baixar_foto_base64(url: str) -> Optional[str]:
    """Baixa foto do produto e retorna base64 (formato exigido pela Evolution API)."""
    if not url or not url.startswith("http"):
        return None
    try:
        import requests  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
        r = requests.get(url, timeout=10)
        if r.status_code != 200 or not r.content:
            return None
        # Otimiza para envio mais rápido
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        img.thumbnail((900, 900))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        log.warning("Falha ao baixar/converter foto: %s", e)
        return None


def enviar_texto(mensagem: str) -> bool:
    """Envia texto puro ao grupo via Evolution API."""
    c = _config()
    if not _configurada():
        return False
    try:
        import requests  # noqa: PLC0415
        # Evolution v2 espera body direto (sem envelope 'textMessage')
        r = requests.post(
            f"{c['url']}/message/sendText/{c['instance']}",
            headers={"apikey": c["key"], "Content-Type": "application/json"},
            json={"number": c["group_id"], "text": mensagem},
            timeout=15,
        )
        if r.status_code in (200, 201):
            log.info("✅ WA API: texto enviado para %s", c["group_id"])
            return True
        # Compatibilidade v1
        r2 = requests.post(
            f"{c['url']}/message/sendText/{c['instance']}",
            headers={"apikey": c["key"], "Content-Type": "application/json"},
            json={"number": c["group_id"], "textMessage": {"text": mensagem}},
            timeout=15,
        )
        if r2.status_code in (200, 201):
            log.info("✅ WA API (v1): texto enviado para %s", c["group_id"])
            return True
        log.warning("WA API erro %d: %s", r.status_code, r.text[:200])
        return False
    except Exception as e:
        from core.error_logger import log_erro  # noqa: PLC0415
        log_erro("wa_api.envio_texto", e, {"grupo": c["group_id"]})
        return False


def enviar_foto_legenda(foto_url: str, legenda: str) -> bool:
    """Envia foto + legenda ao grupo via Evolution API."""
    c = _config()
    if not _configurada():
        return False
    foto_b64 = _baixar_foto_base64(foto_url) if foto_url else None
    if not foto_b64:
        log.info("WA API: sem foto — enviando como texto.")
        return enviar_texto(legenda)
    try:
        import requests  # noqa: PLC0415
        payload = {
            "number": c["group_id"],
            "mediatype": "image",
            "media": foto_b64,
            "caption": legenda,
            "fileName": "oferta.jpg",
        }
        r = requests.post(
            f"{c['url']}/message/sendMedia/{c['instance']}",
            headers={"apikey": c["key"], "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if r.status_code in (200, 201):
            log.info("✅ WA API: foto+legenda enviada para %s", c["group_id"])
            return True
        log.warning("WA API sendMedia erro %d: %s", r.status_code, r.text[:250])
        # Fallback: texto puro
        return enviar_texto(legenda)
    except Exception as e:
        from core.error_logger import log_erro  # noqa: PLC0415
        log_erro("wa_api.envio_foto", e, {"grupo": c["group_id"], "url": foto_url[:80]})
        return False


def enviar_oferta_completa(produto: dict, mensagem: str) -> bool:
    """API pública: envia oferta com foto (se houver) + legenda completa.

    Chamada pelo whatsapp_sender.enviar_para_grupo() como tentativa 1.
    """
    if not _configurada():
        return False
    if not esta_conectada():
        from core.error_logger import log_erro  # noqa: PLC0415
        log_erro(
            "wa_api.desconectada",
            RuntimeError("Instância Evolution não está com estado 'open'"),
            {"instance": _config()["instance"]},
        )
        return False
    foto_url = produto.get("foto") or produto.get("imagem") or ""
    return enviar_foto_legenda(foto_url, mensagem)


def listar_grupos() -> list[dict]:
    """Lista os grupos do WhatsApp com seus JIDs (para configurar .env)."""
    c = _config()
    if not (c["url"] and c["key"]):
        return []
    try:
        import requests  # noqa: PLC0415
        r = requests.get(
            f"{c['url']}/group/fetchAllGroups/{c['instance']}?getParticipants=false",
            headers={"apikey": c["key"]},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return [
            {"id": g.get("id"), "nome": g.get("subject", ""),
             "criador": g.get("owner", "")}
            for g in (data if isinstance(data, list) else data.get("groups", []))
        ]
    except Exception as e:
        log.warning("listar_grupos falhou: %s", e)
        return []
