# -*- coding: utf-8 -*-
"""
Validação anti-golpe e análise de demanda.

Critérios de rejeição:
- Vendedor com reputação vermelha/laranja
- Menos de 10 vendas concluídas
- Desconto > 75% (preço provavelmente inflado artificialmente)
- Preço atual < R$5 (suspeito)
- Título contém palavras de produto falso/réplica

Score de demanda (0-100):
- Quantidade vendida
- Desconto na faixa confiável (15-60%)
- Tem foto de qualidade
- Título descritivo
"""
from __future__ import annotations

_PALAVRAS_SUSPEITAS = {
    "réplica", "replica", "imitação", "imitacao",
    "inspired", "genérico", "generico", "similar",
    "cópia", "copia", "falso", "pirata",
}

_REPUTACOES_RUINS = {"1_red", "2_orange"}


def validar(produto: dict, reputacao: dict) -> tuple[bool, str]:
    """
    Retorna (aprovado, motivo).
    Se aprovado=False o produto não deve ser publicado.
    """
    preco: float = produto.get("preco") or 0
    preco_original: float = produto.get("preco_original") or 0
    desconto_pct: float = produto.get("desconto_pct") or 0
    titulo: str = (produto.get("titulo") or "").lower()

    # Preço absurdamente baixo
    if preco < 5:
        return False, f"preço suspeito R${preco:.2f}"

    # Desconto irreal — sinal clássico de preço inflado artificialmente
    if desconto_pct > 75:
        return False, f"desconto irreal {desconto_pct:.0f}% (possível preço inflado)"

    # Razão preço original / atual > 5x também é sinal de inflação
    if preco_original and preco and (preco_original / preco) > 5:
        return False, f"razão preço suspeita ({preco_original:.0f}/{preco:.0f})"

    # Reputação do vendedor
    nivel: str = reputacao.get("nivel", "")
    if nivel in _REPUTACOES_RUINS:
        return False, f"vendedor com reputação {nivel}"

    # Vendedor sem histórico
    total_vendas: int = reputacao.get("total_vendas") or 0
    if total_vendas < 10:
        return False, f"vendedor com apenas {total_vendas} venda(s) concluída(s)"

    # Palavras que indicam produto falso/réplica
    for palavra in _PALAVRAS_SUSPEITAS:
        if palavra in titulo:
            return False, f"título contém '{palavra}'"

    return True, "ok"


def score_demanda(produto: dict) -> int:
    """Score de demanda 0-100 independente do desconto."""
    score = 0

    # Volume de vendas — principal sinal de demanda real
    qty: int = produto.get("quantidade_vendida") or 0
    if qty >= 1000:
        score += 50
    elif qty >= 500:
        score += 40
    elif qty >= 100:
        score += 30
    elif qty >= 50:
        score += 20
    elif qty >= 10:
        score += 10

    # Desconto na faixa mais confiável
    desc: float = produto.get("desconto_pct") or 0
    if 20 <= desc <= 60:
        score += 30
    elif 15 <= desc < 20 or 60 < desc <= 75:
        score += 15

    # Avaliação média ≥ 4.0
    aval: float = produto.get("avaliacoes") or 0
    if aval >= 4.5:
        score += 15
    elif aval >= 4.0:
        score += 8

    # Foto disponível
    if produto.get("foto"):
        score += 5

    return min(100, score)
