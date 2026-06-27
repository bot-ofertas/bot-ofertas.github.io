# -*- coding: utf-8 -*-
"""
Cliente da API pública do Mercado Livre (sem autenticação necessária).
Busca produtos com desconto, dados do item e reputação do vendedor.
"""
from __future__ import annotations

import os

import requests

_BASE = "https://api.mercadolibre.com"
_TIMEOUT = 15


def _token() -> str:
    """Lê o access token do ambiente. Lança erro claro se não estiver definido."""
    token = os.getenv("ML_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError(
            "ML_ACCESS_TOKEN não está definido no .env\n"
            "  → Rode primeiro: python ml_auth.py"
        )
    return token

# IDs de categoria MLB para monitorar
CATEGORIAS_ML: dict[str, str] = {
    "celulares":   "MLB1051",
    "eletronicos": "MLB1000",
    "informatica": "MLB1648",
    "casa":        "MLB1574",
    "esportes":    "MLB1276",
    "moda":        "MLB1430",
}


def buscar_ofertas(categoria_id: str, desconto_min: int = 15, limite: int = 30) -> list[dict]:
    """Retorna itens com desconto real acima de desconto_min%."""
    try:
        resp = requests.get(
            f"{_BASE}/sites/MLB/search",
            params={
                "category": categoria_id,
                "sort": "relevance",
                "limit": limite,
                "access_token": _token(),
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Erro na busca ML: {e}") from e

    produtos = []
    for item in resp.json().get("results", []):
        preco: float | None = item.get("price")
        preco_original: float | None = item.get("original_price")

        if not preco or not preco_original or preco_original <= preco:
            continue

        desconto_pct = (1 - preco / preco_original) * 100
        if desconto_pct < desconto_min:
            continue

        foto = item.get("thumbnail", "")
        foto = foto.replace("I.jpg", "O.jpg") if foto else None

        produtos.append({
            "ml_id":             item["id"],
            "titulo":            item["title"],
            "preco":             preco,
            "preco_original":    preco_original,
            "desconto_pct":      round(desconto_pct, 1),
            "link":              item.get("permalink", ""),
            "foto":              foto,
            "vendedor_id":       item.get("seller", {}).get("id"),
            "quantidade_vendida": item.get("sold_quantity", 0),
            "avaliacoes":        item.get("reviews", {}).get("rating_average", 0),
            "categoria":         categoria_id,
            "canal":             "geral",
        })

    return produtos


def obter_reputacao_vendedor(vendedor_id: int) -> dict:
    """Retorna nível de reputação, % positivo e total de vendas."""
    try:
        resp = requests.get(f"{_BASE}/users/{vendedor_id}", timeout=_TIMEOUT)
        resp.raise_for_status()
        dados = resp.json()
        rep = dados.get("seller_reputation", {})
        trans = rep.get("transactions", {})
        ratings = trans.get("ratings", {})
        return {
            "nivel":        rep.get("level_id", ""),
            "positivo_pct": float(ratings.get("positive", 0)),
            "total_vendas": int(trans.get("completed", 0)),
            "registro":     dados.get("registration_date", ""),
        }
    except Exception:
        return {"nivel": "", "positivo_pct": 0.0, "total_vendas": 0}
