# -*- coding: utf-8 -*-
"""
Geração de conteúdo completo com IA (Claude Sonnet) para todas as plataformas.

Uma única chamada à API gera simultâneamente:
  - titulo_telegram : título otimizado para Telegram (≤60 chars, emojis, urgência)
  - descricao_telegram: 2-3 linhas com benefícios para Telegram
  - mensagem_whatsapp : post completo formatado para WhatsApp (texto plano, emojis)

Fallback gracioso se ANTHROPIC_API_KEY não estiver configurada.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

log = logging.getLogger(__name__)

_MODELO = "claude-sonnet-4-6"
_MAX_TOKENS = 600
_TIMEOUT = 8.0

_cache: dict[str, dict] = {}
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key or api_key.startswith("sk-ant-..."):
        return None
    try:
        import anthropic
        _client = anthropic.Anthropic(api_key=api_key, timeout=_TIMEOUT)
        return _client
    except Exception:
        return None


def ia_ativa() -> bool:
    return _get_client() is not None


def _chave_cache(produto: dict) -> str:
    return str(produto.get("id") or produto.get("titulo", ""))[:80]


def gerar_conteudo(produto: dict) -> dict:
    """Gera conteúdo para todas as plataformas em uma única chamada de IA.

    Returns:
        {
          "titulo_telegram": str,
          "descricao_telegram": str,
          "mensagem_whatsapp": str,
          "ia_usada": bool
        }
    """
    chave = _chave_cache(produto)
    if chave and chave in _cache:
        return _cache[chave]

    resultado = _fallback(produto)

    client = _get_client()
    if client is None:
        return resultado

    titulo = produto.get("titulo") or ""
    preco = produto.get("preco")
    preco_original = produto.get("preco_original")
    desconto_pct = produto.get("desconto_pct") or 0
    categoria = produto.get("categoria") or "geral"
    cupom = produto.get("cupom") or ""
    fonte = produto.get("fonte") or "ml"
    link = produto.get("link") or produto.get("affiliate_link") or ""

    if preco_original and preco and not desconto_pct:
        desconto_pct = round((1 - preco / preco_original) * 100)

    economia = ""
    if preco_original and preco and preco_original > preco:
        economia = f"R$ {preco_original - preco:.2f}"

    loja = "Amazon Brasil" if fonte == "amazon" else "Mercado Livre"

    preco_str = f"R$ {preco:.2f}" if preco else "não informado"
    preco_orig_str = f"R$ {preco_original:.2f}" if preco_original else "não informado"

    prompt = f"""Você é especialista em copywriting de ofertas para redes sociais brasileiras.
Dados do produto:
- Título: {titulo}
- Preço atual: {preco_str}
- Preço original: {preco_orig_str}
- Desconto: {desconto_pct:.0f}%
- Economia: {economia or 'não calculada'}
- Categoria: {categoria}
- Loja: {loja}
{f'- Cupom: {cupom}' if cupom else ''}
- Link: {link}

Gere conteúdo de alta conversão para 3 formatos. Responda APENAS com JSON válido:

{{
  "titulo_telegram": "título impactante máx 60 chars com 1-2 emojis e urgência",
  "descricao_telegram": "2-3 linhas destacando benefícios reais, desconto e economia. Use emojis. Sem inventar specs.",
  "mensagem_whatsapp": "post completo para grupo WhatsApp (texto plano, sem HTML). Inclua: emoji chamativo, produto, preço, desconto, economia{', cupom em destaque' if cupom else ''}, CTA 'Corre que é por tempo limitado!' e o link no final. Máx 10 linhas."
}}

Regras:
- Português brasileiro informal e empolgante
- Nunca invente especificações técnicas não mencionadas
- Destaque sempre a ECONOMIA em reais
- Use linguagem de escassez/urgência
- título_telegram: máximo EXATO de 60 caracteres"""

    try:
        response = client.messages.create(
            model=_MODELO,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        texto = ""
        for block in response.content:
            if block.type == "text":
                texto = block.text.strip()
                break

        # Extrai JSON da resposta
        if "```json" in texto:
            texto = texto.split("```json")[1].split("```")[0].strip()
        elif "```" in texto:
            texto = texto.split("```")[1].split("```")[0].strip()

        dados = json.loads(texto)

        titulo_tg = str(dados.get("titulo_telegram") or "").strip()[:60]
        desc_tg = str(dados.get("descricao_telegram") or "").strip()
        msg_wa = str(dados.get("mensagem_whatsapp") or "").strip()

        if titulo_tg:
            resultado = {
                "titulo_telegram": titulo_tg,
                "descricao_telegram": desc_tg,
                "mensagem_whatsapp": msg_wa,
                "ia_usada": True,
            }
            if chave:
                _cache[chave] = resultado
            log.info("IA gerou conteúdo para: %s", titulo[:50])

    except Exception as e:
        log.warning("IA falhou para '%s': %s", titulo[:40], e)

    return resultado


def _fallback(produto: dict) -> dict:
    """Conteúdo de fallback sem IA."""
    titulo = produto.get("titulo") or "Oferta especial"
    preco = produto.get("preco")
    preco_original = produto.get("preco_original")
    link = produto.get("link") or produto.get("affiliate_link") or ""
    cupom = produto.get("cupom") or ""
    desconto_pct = produto.get("desconto_pct") or 0

    if preco_original and preco and not desconto_pct:
        desconto_pct = round((1 - preco / preco_original) * 100)

    desc_str = f" -{desconto_pct:.0f}% OFF" if desconto_pct else ""
    preco_str = f" | R${preco:.0f}" if preco else ""

    titulo_curto = titulo[:55]
    titulo_tg = f"🔥 {titulo_curto}{desc_str}"[:60]

    economia = ""
    if preco_original and preco and preco_original > preco:
        economia = f"\n💸 Economia de R$ {preco_original - preco:.2f}"

    msg_wa_linhas = [
        f"🔥 *OFERTA IMPERDÍVEL!*",
        "",
        f"*{titulo[:80]}*",
        "",
    ]
    if preco:
        msg_wa_linhas.append(f"💰 Por apenas R$ {preco:.2f}{desc_str}")
    if economia:
        msg_wa_linhas.append(economia.strip())
    if cupom:
        msg_wa_linhas += ["", f"🏷️ *CUPOM:* `{cupom}`", "↳ Use na finalização!"]
    msg_wa_linhas += ["", "🛒 Corre que é por tempo limitado!", "", f"👉 {link}"]

    return {
        "titulo_telegram": titulo_tg,
        "descricao_telegram": "",
        "mensagem_whatsapp": "\n".join(msg_wa_linhas),
        "ia_usada": False,
    }
