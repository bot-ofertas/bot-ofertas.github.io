# -*- coding: utf-8 -*-
"""
AMAZON PA-API — Product Advertising API oficial (Amazon Creators API),
usada para VALIDAR e ENRIQUECER produtos já raspados com dados oficiais
(preço, título, imagem, disponibilidade) direto da Amazon, em vez de
confiar só no parsing frágil do HTML da página (foi um regex desatualizado
nesse parsing que causou o bug de "0 produtos" corrigido antes).

100% opcional — sem as credenciais configuradas, o bot continua
funcionando normalmente só com o scraper (integrations/amazon_scraper.py).
Nunca lança exceção nem bloqueia a publicação: qualquer falha (rate limit,
ASIN não encontrado, credenciais inválidas) retorna o produto original,
sem alteração.

Pré-requisito da Amazon: a conta de afiliado precisa ter pelo menos 3
vendas qualificadas nos últimos 180 dias para liberar acesso à API.

Setup (só depois de ter as credenciais):
    1. https://affiliate-program.amazon.com.br/assoc_credentials/home
    2. Gera Access Key + Secret Key
    3. No .env:
        AMAZON_PAAPI_ACCESS_KEY=...
        AMAZON_PAAPI_SECRET_KEY=...
    (AMAZON_AFFILIATE_TAG já configurada é reaproveitada como partner tag)
"""
from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("amazon_paapi")

_ACCESS_KEY = os.getenv("AMAZON_PAAPI_ACCESS_KEY", "")
_SECRET_KEY = os.getenv("AMAZON_PAAPI_SECRET_KEY", "")
_PARTNER_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "")

_client = None
_indisponivel_ate = 0.0  # cooldown após erro de credencial/rate-limit persistente
_COOLDOWN_S = 1800  # 30 min


def paapi_ativa() -> bool:
    """True se as credenciais da PA-API estão configuradas."""
    return bool(_ACCESS_KEY and _SECRET_KEY and _PARTNER_TAG)


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not paapi_ativa():
        return None
    try:
        from amazon_creatorsapi import AmazonCreatorsApi, Country  # noqa: PLC0415
        _client = AmazonCreatorsApi(
            credential_id=_ACCESS_KEY,
            credential_secret=_SECRET_KEY,
            version="2.2",
            tag=_PARTNER_TAG,
            country=Country.BR,
            throttling=1.1,  # a Amazon limita bem — evita martelar a API
        )
        return _client
    except Exception as e:
        log.warning("Falha ao inicializar cliente PA-API: %s", e)
        return None


def _extrair_asin(url: str) -> str | None:
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url or "")
    return m.group(1) if m else None


def _valor_seguro(obj, *caminho, default=None):
    """Navega uma cadeia de atributos tolerando None/AttributeError em
    qualquer ponto — os modelos da PA-API têm muitos campos Optional."""
    atual = obj
    for passo in caminho:
        if atual is None:
            return default
        atual = getattr(atual, passo, None)
    return atual if atual is not None else default


def enriquecer_produto(produto: dict) -> dict:
    """Complementa/valida um produto já raspado com dados oficiais via
    PA-API, usando o ASIN extraído do link. Best-effort: qualquer falha
    retorna o produto original sem alteração — o scraper continua sendo a
    fonte de verdade quando a API não está disponível ou falha."""
    import time

    global _indisponivel_ate
    if time.time() < _indisponivel_ate:
        return produto

    client = _get_client()
    if client is None:
        return produto

    asin = _extrair_asin(produto.get("link", ""))
    if not asin:
        return produto

    try:
        itens = client.get_items(asin)
        if not itens:
            return produto
        item = itens[0]

        atualizado = dict(produto)
        atualizado["_paapi_verificado"] = True

        titulo_attr = _valor_seguro(item, "item_info", "title")
        titulo = _valor_seguro(titulo_attr, "display_value") or (str(titulo_attr) if titulo_attr else None)
        if titulo:
            atualizado["titulo"] = titulo

        foto_url = _valor_seguro(item, "images", "primary", "large", "url")
        if foto_url:
            atualizado["foto"] = foto_url

        listings = _valor_seguro(item, "offers_v2", "listings")
        if listings:
            listing = listings[0]
            preco = _valor_seguro(listing, "price", "money", "amount")
            if preco is not None:
                atualizado["preco"] = float(preco)
            preco_original = _valor_seguro(listing, "price", "saving_basis", "money", "amount")
            if preco_original is not None:
                atualizado["preco_original"] = float(preco_original)
            disponibilidade = _valor_seguro(listing, "availability", "type")
            if disponibilidade is not None:
                atualizado["disponivel"] = str(disponibilidade).lower() in ("now", "available")

        log.info("PA-API verificou %s (ASIN %s)", produto.get("titulo", "")[:40], asin)
        return atualizado

    except Exception as e:
        msg = str(e).lower()
        if any(termo in msg for termo in ("unauthorized", "invalid", "credential", "throttl", "too many")):
            _indisponivel_ate = time.time() + _COOLDOWN_S
            log.warning("PA-API indisponível (%s) — pausando por %d min, scraper continua sozinho",
                        str(e)[:100], _COOLDOWN_S // 60)
        else:
            log.debug("PA-API enriquecer_produto falhou para ASIN %s: %s", asin, e)
        return produto
