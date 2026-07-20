# -*- coding: utf-8 -*-
"""
Scraper das páginas públicas de ofertas do Mercado Livre.
Lê as páginas de ofertas por categoria (sem API, sem autenticação)
e gera links de afiliado automaticamente para cada produto encontrado.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from dotenv import load_dotenv

load_dotenv()

_AFFILIATE_TOOL_ID = os.getenv("ML_AFFILIATE_TOOL_ID", "47114387")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

# URLs das páginas de ofertas por nicho
PAGINAS_OFERTAS: dict[str, str] = {
    "celulares":    "https://www.mercadolivre.com.br/ofertas/celulares-e-telefones",
    "eletronicos":  "https://www.mercadolivre.com.br/ofertas/eletronicos-audio-e-video",
    "informatica":  "https://www.mercadolivre.com.br/ofertas/computadores-e-acessorios",
    "casa":         "https://www.mercadolivre.com.br/ofertas/casa",
    "esportes":     "https://www.mercadolivre.com.br/ofertas/esportes-e-fitness",
}


def _get_html(url: str) -> str:
    import gzip
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        if resp.info().get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")


def _gerar_link_afiliado(url_produto: str) -> str:
    """Adiciona parâmetros de afiliado ao link do produto."""
    separador = "&" if "?" in url_produto else "?"
    return (
        f"{url_produto}{separador}"
        f"matt_tool={_AFFILIATE_TOOL_ID}"
        f"&matt_word=oferta&matt_source=bot_telegram"
    )


def _extrair_produtos_do_html(html: str, nicho: str) -> list[dict]:
    """Extrai produtos do JSON embutido pelo ML no HTML das páginas de oferta."""
    produtos: list[dict] = []

    # ML embute dados em <script type="application/json"> ou __NEXT_DATA__
    blocos_json = re.findall(
        r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    # Também tenta __NEXT_DATA__
    m_next = re.search(
        r'<script id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    if m_next:
        blocos_json.append(m_next.group(1))

    for bloco in blocos_json:
        try:
            dados = json.loads(bloco)
        except (json.JSONDecodeError, ValueError):
            continue
        itens = _extrair_itens_do_json(dados)
        produtos.extend(itens)
        if produtos:
            break

    # Fallback: regex direta no HTML para JSON embutido em variável JS
    if not produtos:
        m_state = re.search(r'window\.__PRELOADED_STATE__\s*=\s*({.*?})\s*;', html, re.DOTALL)
        if m_state:
            try:
                dados = json.loads(m_state.group(1))
                produtos.extend(_extrair_itens_do_json(dados))
            except Exception:
                pass

    # Normaliza e adiciona link de afiliado
    resultado = []
    for p in produtos:
        if not p.get("titulo") or not p.get("link"):
            continue
        p["link"] = _gerar_link_afiliado(p["link"].split("?")[0])
        p["categoria"] = nicho
        p["canal"] = "geral"
        resultado.append(p)

    return resultado


def _extrair_itens_do_json(obj, profundidade: int = 0) -> list[dict]:
    """Percorre o JSON recursivamente procurando estruturas de produto ML."""
    if profundidade > 10:
        return []
    encontrados: list[dict] = []

    if isinstance(obj, dict):
        # Detecta estrutura de produto ML
        if obj.get("id") and obj.get("title") and (obj.get("price") or obj.get("original_price")):
            preco = obj.get("price") or obj.get("original_price", 0)
            preco_original = obj.get("original_price")
            permalink = obj.get("permalink") or obj.get("url") or ""

            thumbnail = obj.get("thumbnail") or obj.get("picture") or ""
            if isinstance(thumbnail, dict):
                thumbnail = thumbnail.get("url", "")
            if thumbnail:
                thumbnail = thumbnail.replace("I.jpg", "O.jpg").replace("W.jpg", "O.jpg")

            if permalink and preco:
                encontrados.append({
                    "ml_id":              obj["id"],
                    "titulo":             obj.get("title", ""),
                    "preco":              float(preco),
                    "preco_original":     float(preco_original) if preco_original else None,
                    "desconto_pct":       round((1 - float(preco) / float(preco_original)) * 100, 1)
                                          if preco_original and float(preco_original) > float(preco) else 0.0,
                    "link":               permalink,
                    "foto":               thumbnail or None,
                    "vendedor_id":        obj.get("seller", {}).get("id") if isinstance(obj.get("seller"), dict) else None,
                    "quantidade_vendida": obj.get("sold_quantity", 0),
                    "avaliacoes":         obj.get("reviews", {}).get("rating_average", 0)
                                          if isinstance(obj.get("reviews"), dict) else 0,
                })
        else:
            for v in obj.values():
                encontrados.extend(_extrair_itens_do_json(v, profundidade + 1))

    elif isinstance(obj, list):
        for item in obj:
            encontrados.extend(_extrair_itens_do_json(item, profundidade + 1))

    return encontrados


def buscar_ofertas_pagina(nicho: str, desconto_min: int = 10) -> list[dict]:
    """
    Busca produtos com desconto na página pública de ofertas do ML.
    Retorna lista de produtos prontos para validação.
    """
    url = PAGINAS_OFERTAS.get(nicho)
    if not url:
        raise ValueError(f"Nicho '{nicho}' não configurado. Disponíveis: {list(PAGINAS_OFERTAS)}")

    html = _get_html(url)
    produtos = _extrair_produtos_do_html(html, nicho)

    # Filtra por desconto mínimo
    return [p for p in produtos if p.get("desconto_pct", 0) >= desconto_min]
