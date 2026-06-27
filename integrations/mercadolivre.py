# -*- coding: utf-8 -*-
"""
Cliente da API do Mercado Livre.
Busca produtos com desconto, dados do item e reputação do vendedor.
"""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

load_dotenv()

_BASE = "https://api.mercadolibre.com"
_TIMEOUT = 15

# Termos de busca por nicho — mais confiável que busca por categoria
TERMOS_BUSCA: dict[str, list[str]] = {
    "celulares":   ["smartphone", "celular iphone", "samsung galaxy"],
    "eletronicos": ["fone bluetooth", "smartwatch", "tablet"],
    "informatica": ["notebook", "ssd", "monitor gamer"],
    "casa":        ["airfryer", "aspirador robot", "cafeteira"],
    "esportes":    ["bike eletrica", "esteira", "tenis corrida"],
}


def _headers() -> dict:
    token = os.getenv("ML_ACCESS_TOKEN", "").strip("'\"")
    if not token:
        raise RuntimeError(
            "ML_ACCESS_TOKEN não definido no .env\n"
            "  → Rode: python ml_auth.py"
        )
    return {"Authorization": f"Bearer {token}"}


def buscar_por_termo(termo: str, desconto_min: int = 15, limite: int = 20) -> list[dict]:
    """Busca produtos por palavra-chave e filtra por desconto real."""
    try:
        resp = requests.get(
            f"{_BASE}/sites/MLB/search",
            params={"q": termo, "sort": "relevance", "limit": limite},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Erro na busca '{termo}': {e}") from e

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
            "ml_id":              item["id"],
            "titulo":             item["title"],
            "preco":              preco,
            "preco_original":     preco_original,
            "desconto_pct":       round(desconto_pct, 1),
            "link":               item.get("permalink", ""),
            "foto":               foto,
            "vendedor_id":        item.get("seller", {}).get("id"),
            "quantidade_vendida": item.get("sold_quantity", 0),
            "avaliacoes":         item.get("reviews", {}).get("rating_average", 0),
            "categoria":          "geral",
            "canal":              "geral",
        })

    return produtos


def buscar_ofertas_nicho(nicho: str, desconto_min: int = 15) -> list[dict]:
    """Busca por todos os termos de um nicho, remove duplicatas por ml_id."""
    termos = TERMOS_BUSCA.get(nicho, [nicho])
    vistos: set[str] = set()
    todos: list[dict] = []
    for termo in termos:
        try:
            itens = buscar_por_termo(termo, desconto_min)
            for item in itens:
                if item["ml_id"] not in vistos:
                    vistos.add(item["ml_id"])
                    todos.append(item)
        except RuntimeError as e:
            raise e
    return todos


def obter_reputacao_vendedor(vendedor_id: int | None) -> dict:
    """Retorna nível de reputação, % positivo e total de vendas."""
    if not vendedor_id:
        return {"nivel": "", "positivo_pct": 0.0, "total_vendas": 0}
    try:
        resp = requests.get(
            f"{_BASE}/users/{vendedor_id}",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        dados = resp.json()
        rep   = dados.get("seller_reputation", {})
        trans = rep.get("transactions", {})
        ratings = trans.get("ratings", {})
        return {
            "nivel":        rep.get("level_id", ""),
            "positivo_pct": float(ratings.get("positive", 0)),
            "total_vendas": int(trans.get("completed", 0)),
        }
    except Exception:
        return {"nivel": "", "positivo_pct": 0.0, "total_vendas": 0}
