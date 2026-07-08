# -*- coding: utf-8 -*-
"""
PRICE ALERTS — detecta quedas de preço significativas para priorizar postagem.

Aproveita a tabela precos_historico do BD (já preenchida pelo rastreador).
Retorna produtos que caíram de preço nos últimos N dias.
"""
from __future__ import annotations

import logging
from typing import Optional

from core import database as db

log = logging.getLogger("price_alerts")


def queda_significativa(produto_id: str, preco_atual: float, dias: int = 30) -> Optional[dict]:
    """Se o preço atual for menor que o menor histórico em N dias, retorna dict.

    Returns:
        {
            "queda_pct": 15,      # % de queda vs. menor anterior
            "menor_antes": 199.00, # menor preço no período anterior
            "atual": 169.00,
            "pontos": 12,         # quantas leituras temos
        }
        None se não há histórico suficiente.
    """
    if not produto_id or not preco_atual or preco_atual <= 0:
        return None
    hist = db.historico_preco(produto_id, dias=dias)
    if hist.get("pontos", 0) < 3:  # precisa histórico mínimo
        return None
    menor = hist.get("menor")
    if not menor:
        return None
    if preco_atual < menor * 0.97:  # 3% mais barato que o menor histórico
        queda_pct = round((1 - preco_atual / menor) * 100, 1)
        return {
            "queda_pct": queda_pct,
            "menor_antes": round(menor, 2),
            "atual": round(preco_atual, 2),
            "pontos": hist["pontos"],
            "dias": dias,
        }
    return None


def score_boost_por_queda(produto: dict) -> int:
    """Retorna bônus de score para produtos em queda de preço.

    Queda >30% → +25 pontos (super-oferta)
    Queda >20% → +15
    Queda >10% → +10
    Menor preço em 30d → +5 (mesmo sem queda significativa)
    """
    pid = produto.get("id", "")
    preco = produto.get("preco")
    if not pid or not preco:
        return 0
    queda = queda_significativa(pid, float(preco))
    if queda:
        p = queda["queda_pct"]
        if p >= 30:
            return 25
        if p >= 20:
            return 15
        if p >= 10:
            return 10
    # Menor do período
    hist = db.historico_preco(pid, dias=30)
    if hist.get("e_menor_periodo"):
        return 5
    return 0


def listar_maiores_quedas(limite: int = 10) -> list[dict]:
    """Lista as maiores quedas de preço recentes no BD (para dashboard/n8n)."""
    try:
        with db._conn() as con:
            rows = con.execute("""
                SELECT p.id, p.titulo, p.preco, p.preco_original, p.foto,
                       p.affiliate_link, p.adicionado_em
                FROM produtos p
                WHERE p.preco IS NOT NULL AND p.preco_original IS NOT NULL
                  AND p.preco < p.preco_original * 0.7
                ORDER BY (p.preco_original - p.preco) / p.preco_original DESC
                LIMIT ?
            """, (limite,)).fetchall()
        return [{
            "id": r["id"], "titulo": r["titulo"],
            "preco": r["preco"], "preco_original": r["preco_original"],
            "desconto_pct": round((1 - r["preco"] / r["preco_original"]) * 100, 1),
            "foto": r["foto"], "link": r["affiliate_link"],
        } for r in rows]
    except Exception as e:
        log.warning("listar_maiores_quedas: %s", e)
        return []
