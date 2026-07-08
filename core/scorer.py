# -*- coding: utf-8 -*-
"""
Pontuacao de ofertas (0-100+).

Pesos base:
  desconto%           0-40 pts  (faixa ideal 20-60%, fora disso 70%)
  economia R$         0-20 pts
  comissao ML         0-20 pts
  foto                  10 pts
  titulo                10 pts
  avaliacao (0-5)       15 pts
  quantidade vendida    20 pts
  bonus categoria       5 pts
  penalidade preco     -10 pts
"""
from __future__ import annotations

COMISSOES_ML: dict[str, float] = {
    "beleza": 0.16,
    "moda": 0.14,
    "casa": 0.12,
    "moveis": 0.12,
    "brinquedos": 0.12,
    "esportes": 0.10,
    "eletronicos": 0.08,
    "informatica": 0.08,
    "games": 0.08,
    "automotivo": 0.06,
    "geral": 0.08,
}

# Categorias com comissoes mais altas que merecem bonus extra
CATEGORIAS_PREMIUM: frozenset[str] = frozenset({"beleza", "moda"})


def _score_desconto(pct: float) -> int:
    """Retorna pontuacao de desconto (max 40 pts) com faixa ideal 20-60%."""
    if pct <= 0:
        return 0
    base = min(40, int(pct))
    if 20.0 <= pct <= 60.0:
        return base
    if (15.0 <= pct < 20.0) or (60.0 < pct <= 75.0):
        return int(base * 0.7)
    # abaixo de 15% ou acima de 75%: proporcional sem bonus de faixa
    return int(base * 0.5)


def _score_avaliacao(avaliacao: float | None) -> int:
    """Retorna pontuacao de avaliacao (max 15 pts)."""
    if avaliacao is None:
        return 0
    if avaliacao >= 4.5:
        return 15
    if avaliacao >= 4.0:
        return 10
    if avaliacao >= 3.5:
        return 5
    return 0


def _score_quantidade_vendida(qtd: int | None) -> int:
    """Retorna pontuacao por quantidade de vendas (max 20 pts)."""
    if qtd is None:
        return 0
    if qtd >= 1000:
        return 20
    if qtd >= 500:
        return 15
    if qtd >= 100:
        return 10
    if qtd >= 50:
        return 5
    return 0


def calcular_score(produto: dict) -> int:
    """Calcula a pontuacao de atratividade de um produto (0-100).

    Parametros reconhecidos em ``produto``:
      preco            (float)  -- preco atual
      preco_original   (float)  -- preco antes do desconto
      categoria        (str)    -- categoria do produto
      foto             (str)    -- URL ou caminho da foto (presenca vale pts)
      titulo           (str)    -- titulo do produto
      avaliacoes       (float)  -- nota media 0-5
      quantidade_vendida (int)  -- total de unidades vendidas
    """
    score = 0

    preco: float | None = produto.get("preco")
    preco_original: float | None = produto.get("preco_original")

    if preco and preco_original and preco_original > preco:
        pct_desconto = (1 - preco / preco_original) * 100
        score += _score_desconto(pct_desconto)

        economia = preco_original - preco
        score += min(20, int(economia / 25))

    categoria = (produto.get("categoria") or "geral").lower()
    comissao = COMISSOES_ML.get(categoria, 0.08)
    score += int(comissao * 125)  # 16% -> 20 pts, 8% -> 10 pts

    if produto.get("foto"):
        score += 10

    titulo = produto.get("titulo", "")
    if len(titulo) >= 20:
        score += 5
    if len(titulo) >= 50:
        score += 5

    score += _score_avaliacao(produto.get("avaliacoes"))
    score += _score_quantidade_vendida(produto.get("quantidade_vendida"))

    if categoria in CATEGORIAS_PREMIUM:
        score += 5

    if preco and preco < 30.0:
        score -= 10

    return min(100, max(0, score))


def classificar_score(score: int) -> str:
    """Classifica a oferta a partir do score (0-100).

    Returns: 'excelente' | 'boa' | 'media' | 'ruim'
      excelente (>=85): publicar com destaque
      boa       (>=65): publicar normalmente
      media     (>=45): guardar para análise
      ruim      (<45):  descartar
    """
    if score >= 85:
        return "excelente"
    if score >= 65:
        return "boa"
    if score >= 45:
        return "media"
    return "ruim"


def score_inteligente(produto: dict) -> int:
    """Score enriquecido: base + queda de preço + Google Trends.

    Chame no lugar do calcular_score() para priorizar ofertas mais lucrativas.
    Adiciona até +40 pts se produto está viral e em queda de preço.
    """
    base = calcular_score(produto)
    # Boost por queda de preço (usa histórico do BD)
    try:
        from core.price_alerts import score_boost_por_queda  # noqa: PLC0415
        base += score_boost_por_queda(produto)
    except Exception:
        pass
    # Boost por trending (Google Trends BR)
    try:
        from core.trending import score_trending  # noqa: PLC0415
        titulo = produto.get("titulo", "")
        if titulo:
            # Extrai as 3 primeiras palavras (marca+modelo geralmente)
            keywords = " ".join(titulo.split()[:3])
            trend = score_trending(keywords)
            # trend >70 = viral: +15; >50: +5
            if trend >= 70:
                base += 15
            elif trend >= 50:
                base += 5
    except Exception:
        pass
    return min(base, 150)  # cap em 150


def selo_classificacao(score: int) -> tuple[str, str]:
    """Retorna (emoji, rótulo) para exibir a classificação ao usuário."""
    return {
        "excelente": ("🔥", "Oferta Imperdível"),
        "boa":       ("✅", "Boa Oferta"),
        "media":     ("👍", "Oferta OK"),
        "ruim":      ("⚪", "Comum"),
    }[classificar_score(score)]


def top_produtos(produtos: list[dict], n: int = 10) -> list[dict]:
    """Ordena ``produtos`` por score decrescente e retorna os top ``n``.

    Cada produto do retorno recebe o campo ``_score`` com o valor calculado.
    A lista original nao e modificada.
    """
    scored = []
    for p in produtos:
        entry = dict(p)
        entry["_score"] = calcular_score(p)
        scored.append(entry)
    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored[:n]
