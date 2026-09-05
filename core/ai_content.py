# -*- coding: utf-8 -*-
"""
Geração de conteúdo completo com IA (Claude Sonnet) para todas as plataformas.

Uma única chamada à API gera simultâneamente:
  - titulo_telegram : título otimizado para Telegram (≤60 chars, emojis, urgência)
  - descricao_telegram: 2-3 linhas com benefícios para Telegram
  - mensagem_whatsapp : post completo formatado para WhatsApp (texto plano, emojis)

O modelo NUNCA escreve URL: ele usa o marcador `{LINK}` e o link real de
afiliado é injetado e conferido aqui (`core/ai_safety.py`) — ver Regras 3, 4
e 7 do CLAUDE.md e a docstring daquele módulo.

Fallback gracioso se ANTHROPIC_API_KEY não estiver configurada.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from dotenv import load_dotenv

from core.ai_safety import (
    PLACEHOLDER_LINK,
    aplicar_link,
    link_afiliado_valido,
    link_preservado,
    numero as _num,
    remover_urls,
)

load_dotenv()

log = logging.getLogger(__name__)

_MODELO = "claude-sonnet-4-6"
_MAX_TOKENS = 600
_TIMEOUT = 8.0
_COOLDOWN_S = 1800  # 30 min — evita martelar a API quando sem crédito/billing

_SYSTEM = (
    "Você é especialista em copywriting de ofertas para redes sociais "
    "brasileiras. Responde sempre com um único objeto JSON válido, sem "
    "texto antes ou depois, sem cercas de código."
)

_cache: dict[str, dict] = {}
_client = None
_bloqueado_ate = 0.0


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
    """Chave que muda quando o conteúdo do post muda.

    Só o id/título não bastava: o rastreador roda em loop por horas, e o
    mesmo produto republicado depois de mudar de preço reusava a cópia
    antiga em cache — post anunciando um preço que já não existe mais
    (Regra 7).
    """
    base = str(produto.get("id") or produto.get("titulo", ""))[:80]
    if not base:
        return ""
    preco = _num(produto.get("preco"))
    desconto = _num(produto.get("desconto_pct"))
    link = str(produto.get("link") or produto.get("affiliate_link") or "")[-40:]
    return f"{base}|{preco}|{desconto}|{produto.get('cupom') or ''}|{link}"


def _extrair_json(texto: str) -> dict:
    """Lê o JSON da resposta, com ou sem cerca de código e com prefill.

    Como a chamada usa prefill (`{`), a resposta normalmente já vem sem a
    chave de abertura — mas o modelo pode devolver o objeto inteiro se o
    prefill for ignorado. Aceita os dois casos.
    """
    bruto = (texto or "").strip()
    if not bruto:
        raise ValueError("resposta vazia")

    if "```json" in bruto:
        bruto = bruto.split("```json")[1].split("```")[0].strip()
    elif "```" in bruto:
        bruto = bruto.split("```")[1].split("```")[0].strip()

    if not bruto.startswith("{"):
        bruto = "{" + bruto
    if not bruto.endswith("}"):
        # max_tokens cortou o fim: tenta recuperar até a última chave fechada.
        corte = bruto.rfind("}")
        if corte == -1:
            raise ValueError("JSON truncado sem objeto fechado")
        bruto = bruto[: corte + 1]

    return json.loads(bruto)


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
    global _bloqueado_ate
    chave = _chave_cache(produto)
    if chave and chave in _cache:
        return _cache[chave]

    resultado = _fallback(produto)

    if time.time() < _bloqueado_ate:
        return resultado

    client = _get_client()
    if client is None:
        return resultado

    titulo = produto.get("titulo") or ""
    preco = _num(produto.get("preco"))
    preco_original = _num(produto.get("preco_original"))
    desconto_pct = _num(produto.get("desconto_pct")) or 0.0
    categoria = produto.get("categoria") or "geral"
    cupom = produto.get("cupom") or ""
    fonte = produto.get("fonte") or "ml"
    link = produto.get("link") or produto.get("affiliate_link") or ""

    # O link publicado é responsabilidade do rastreador (rastreador.py:219 já
    # barra link sem afiliado), mas se chegar quebrado aqui é falha silenciosa
    # de comissão — loga e segue com o fallback determinístico.
    if link and not link_afiliado_valido(link, fonte):
        log.warning("Link sem parâmetro de afiliado válido: %s", link[:120])
        try:
            from core.error_logger import log_erro  # noqa: PLC0415
            log_erro(
                "ai_content.link_sem_afiliado",
                ValueError("link sem parâmetro de afiliado como query"),
                {"link": link[:200], "fonte": fonte},
            )
        except Exception:
            pass

    if preco_original and preco and not desconto_pct:
        desconto_pct = round((1 - preco / preco_original) * 100)

    economia = ""
    if preco_original and preco and preco_original > preco:
        economia = f"R$ {preco_original - preco:.2f}"

    loja = "Amazon Brasil" if fonte == "amazon" else "Mercado Livre"

    preco_str = f"R$ {preco:.2f}" if preco else "não informado"
    preco_orig_str = f"R$ {preco_original:.2f}" if preco_original else "não informado"
    linha_cupom = f"\n- Cupom: {cupom}" if cupom else ""
    trecho_cupom = ", cupom em destaque" if cupom else ""

    prompt = f"""Dados do produto:
- Título: {titulo}
- Preço atual: {preco_str}
- Preço original: {preco_orig_str}
- Desconto: {desconto_pct:.0f}%
- Economia: {economia or 'não calculada'}
- Categoria: {categoria}
- Loja: {loja}{linha_cupom}

Gere conteúdo de alta conversão para 3 formatos. Responda APENAS com JSON válido:

{{
  "titulo_telegram": "título impactante máx 60 chars com 1-2 emojis e urgência",
  "descricao_telegram": "2-3 linhas destacando benefícios reais, desconto e economia. Use emojis. Sem inventar specs.",
  "mensagem_whatsapp": "post completo para grupo WhatsApp (texto plano, sem HTML). Inclua: emoji chamativo, produto, preço, desconto, economia{trecho_cupom}, CTA 'Corre que é por tempo limitado!' e o marcador {PLACEHOLDER_LINK} sozinho na última linha. Máx 10 linhas."
}}

Regras:
- Português brasileiro informal e empolgante
- Nunca invente especificações técnicas não mencionadas
- Destaque sempre a ECONOMIA em reais
- Use linguagem de escassez/urgência
- titulo_telegram: máximo EXATO de 60 caracteres
- NUNCA escreva uma URL, domínio ou link encurtado em nenhum dos campos.
  O link da oferta entra depois, no lugar do marcador {PLACEHOLDER_LINK} —
  qualquer link escrito por você é descartado e o post vai sem link."""

    try:
        response = client.messages.create(
            model=_MODELO,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "{"},  # prefill: força JSON
            ],
        )
        texto = ""
        for block in response.content:
            if block.type == "text":
                texto = block.text.strip()
                break

        dados = _extrair_json(texto)

        # Telegram monta o link por conta própria (integrations/telegram_bot.py):
        # URL vinda do modelo aqui só pode ser alucinação — sai fora.
        titulo_tg = remover_urls(str(dados.get("titulo_telegram") or "").strip())[:60]
        desc_tg = remover_urls(str(dados.get("descricao_telegram") or "").strip())
        msg_wa = aplicar_link(str(dados.get("mensagem_whatsapp") or "").strip(), link)

        # Última barreira: a mensagem do WhatsApp vai como mensagem_override,
        # sem passar por montar_mensagem_wa(). Se o link exato não estiver
        # lá (ou sobrou outra URL), publica o fallback determinístico.
        if link and not link_preservado(msg_wa, link):
            log.warning("IA devolveu mensagem sem o link de afiliado — usando fallback")
            msg_wa = ""

        if titulo_tg:
            resultado = {
                "titulo_telegram": titulo_tg,
                "descricao_telegram": desc_tg,
                "mensagem_whatsapp": msg_wa or resultado["mensagem_whatsapp"],
                "ia_usada": True,
            }
            if chave:
                _cache[chave] = resultado
            log.info("IA gerou conteúdo para: %s", titulo[:50])

    except Exception as e:
        msg = str(e)
        if "credit balance" in msg.lower() or "insufficient_quota" in msg.lower():
            _bloqueado_ate = time.time() + _COOLDOWN_S
            log.warning(
                "IA sem crédito — pausando chamadas por %d min (fallback ativo): %s",
                _COOLDOWN_S // 60, msg[:150],
            )
        else:
            log.warning("IA falhou para '%s': %s", titulo[:40], e)

    return resultado


def _fallback(produto: dict) -> dict:
    """Conteúdo de fallback sem IA."""
    titulo = produto.get("titulo") or "Oferta especial"
    preco = _num(produto.get("preco"))
    preco_original = _num(produto.get("preco_original"))
    link = produto.get("link") or produto.get("affiliate_link") or ""
    cupom = produto.get("cupom") or ""
    desconto_pct = _num(produto.get("desconto_pct")) or 0.0

    if preco_original and preco and not desconto_pct:
        desconto_pct = round((1 - preco / preco_original) * 100)

    desc_str = f" -{desconto_pct:.0f}% OFF" if desconto_pct else ""

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
