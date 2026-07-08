# -*- coding: utf-8 -*-
"""
TRENDING — detecta produtos VIRAIS via Google Trends + termos em alta.

Uso:
    from core.trending import score_trending, produtos_em_alta
    score = score_trending("Airfryer 12L")   # 0-100 baseado em interesse
    produtos_em_alta("Casa e Cozinha")       # top 10 palavras-chave da semana
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache

log = logging.getLogger("trending")

_CACHE_TTL = 3600  # 1h
_last_error_ts = 0.0


def _pytrends():
    """Instancia pytrends com fallback silencioso se biblioteca faltar."""
    try:
        from pytrends.request import TrendReq  # noqa: PLC0415
        return TrendReq(hl="pt-BR", tz=180, retries=1, backoff_factor=0.3)
    except Exception as e:
        log.debug("pytrends indisponível: %s", e)
        return None


@lru_cache(maxsize=500)
def _cached_score(query: str, bucket: int) -> int:
    """bucket muda a cada hora para invalidar o cache."""
    tr = _pytrends()
    if tr is None:
        return 50  # neutro se lib faltando
    try:
        tr.build_payload([query], timeframe="now 7-d", geo="BR")
        df = tr.interest_over_time()
        if df is None or df.empty or query not in df:
            return 50
        # Média dos últimos 7 dias (0-100)
        media = float(df[query].mean())
        # Se últimos 3 dias > média: tendência crescente → boost
        ultimos = float(df[query].tail(3).mean())
        if ultimos > media * 1.2:
            return min(100, int(ultimos + 10))
        return int(media)
    except Exception as e:
        global _last_error_ts
        _last_error_ts = time.time()
        log.debug("score_trending falhou para %r: %s", query[:40], e)
        return 50


def score_trending(query: str) -> int:
    """Retorna 0-100. Alto = produto em alta no Google Brasil."""
    if not query or len(query) < 3:
        return 50
    # Rate limit auto: se erro recente, retorna neutro por 5 min
    if _last_error_ts and (time.time() - _last_error_ts < 300):
        return 50
    q = query.strip()[:60]  # Google Trends limita tamanho
    bucket = int(time.time() // _CACHE_TTL)
    return _cached_score(q, bucket)


def produtos_em_alta(categoria: str = "") -> list[str]:
    """Retorna termos em alta na categoria (BR) — bom para pesquisar novos produtos."""
    tr = _pytrends()
    if tr is None:
        return []
    try:
        # Trending searches gerais
        df = tr.trending_searches(pn="brazil")
        if df is None or df.empty:
            return []
        return df.iloc[:, 0].astype(str).head(10).tolist()
    except Exception as e:
        log.debug("produtos_em_alta falhou: %s", e)
        return []
