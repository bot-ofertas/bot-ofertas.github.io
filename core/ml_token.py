# -*- coding: utf-8 -*-
"""
ML TOKEN — token da API do Mercado Livre com renovação automática.

Problema: `ml_auth.py` pega um token e grava em `ML_ACCESS_TOKEN` no `.env`.
O token do fluxo *client credentials* do ML dura ~6 horas. Depois disso toda
chamada volta `401 invalid_token` e o único conserto era alguém rodar
`python ml_auth.py` à mão — num bot que roda sozinho de madrugada, isso
significa horas de scraping falhando em silêncio.

Aqui o token é obtido sob demanda e guardado em memória com o horário de
expiração. Renova sozinho **10 minutos antes** de vencer (margem para não
usar um token que expira no meio da requisição).

O fluxo client credentials não emite `refresh_token` — renovar é pedir um
token novo com as mesmas credenciais, que é exatamente o que `_renovar()`
faz. Não há nada a "atualizar": é um POST igual ao primeiro.

Ordem de resolução:
  1. `ML_APP_ID` + `ML_APP_SECRET` no .env → token renovável (recomendado);
  2. `ML_ACCESS_TOKEN` fixo no .env → usado como está (compatibilidade com
     quem já rodou `ml_auth.py`), sem renovação possível.
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("ml_token")

_URL_TOKEN = "https://api.mercadolibre.com/oauth/token"

# Renova este tanto de segundos ANTES do vencimento real.
MARGEM_S = 600

_lock = threading.Lock()
_cache: dict = {"token": "", "expira_em": 0.0}


class SemCredenciaisML(RuntimeError):
    """Nem app_id/secret nem token fixo disponíveis."""


def _credenciais() -> tuple[str, str]:
    return (
        os.getenv("ML_APP_ID", "").strip().strip("'\""),
        os.getenv("ML_APP_SECRET", "").strip().strip("'\""),
    )


def _renovar() -> str:
    """Pede um token novo ao Mercado Livre. Levanta em caso de falha."""
    from core.net import FalhaDeRede, post  # noqa: PLC0415

    app_id, app_secret = _credenciais()
    try:
        r = post(
            _URL_TOKEN,
            data={
                "grant_type": "client_credentials",
                "client_id": app_id,
                "client_secret": app_secret,
            },
            headers={"Accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"},
            tentativas=3,
            timeout=15,
        )
    except FalhaDeRede as e:
        raise SemCredenciaisML(f"não consegui falar com o ML: {e}") from e

    if r.status_code != 200:
        raise SemCredenciaisML(
            f"ML recusou as credenciais (HTTP {r.status_code}): {r.text[:200]}"
        )

    dados = r.json()
    token = dados.get("access_token", "")
    if not token:
        raise SemCredenciaisML(f"resposta sem access_token: {str(dados)[:200]}")

    # `expires_in` costuma vir 21600 (6h). O default cobre o caso de o ML
    # omitir o campo: melhor renovar cedo demais do que usar token morto.
    validade = int(dados.get("expires_in", 21600))
    _cache["token"] = token
    _cache["expira_em"] = time.time() + max(validade - MARGEM_S, 60)
    log.info("Token do ML renovado — válido por %.1fh (renova em %.1fh)",
             validade / 3600, (validade - MARGEM_S) / 3600)
    return token


def token(forcar: bool = False) -> str:
    """Devolve um token válido, renovando se necessário.

    `forcar=True` descarta o cache — use ao receber 401 do ML, que é o único
    sinal confiável de que o token morreu antes da hora prevista.
    """
    with _lock:
        if not forcar and _cache["token"] and time.time() < _cache["expira_em"]:
            return _cache["token"]

        app_id, app_secret = _credenciais()
        if app_id and app_secret:
            try:
                return _renovar()
            except SemCredenciaisML as e:
                # Com um token fixo no .env ainda dá pra tentar seguir; sem
                # ele, não há o que fazer além de propagar.
                fixo = os.getenv("ML_ACCESS_TOKEN", "").strip().strip("'\"")
                if fixo:
                    log.warning("Renovação falhou (%s) — usando ML_ACCESS_TOKEN do .env", e)
                    return fixo
                raise

        fixo = os.getenv("ML_ACCESS_TOKEN", "").strip().strip("'\"")
        if fixo:
            return fixo

        raise SemCredenciaisML(
            "Sem credenciais do Mercado Livre. Defina ML_APP_ID e ML_APP_SECRET "
            "no .env (token renovado sozinho) ou rode `python ml_auth.py` para "
            "gravar um ML_ACCESS_TOKEN fixo."
        )


def cabecalhos(forcar: bool = False) -> dict:
    """Header de Authorization pronto."""
    return {"Authorization": f"Bearer {token(forcar)}"}


def invalidar() -> None:
    """Descarta o token em cache (chamado ao receber 401)."""
    with _lock:
        _cache["token"] = ""
        _cache["expira_em"] = 0.0


def status() -> dict:
    """Estado do token — entra no /health."""
    app_id, app_secret = _credenciais()
    restante = max(0, _cache["expira_em"] - time.time()) if _cache["token"] else 0
    return {
        "renovavel": bool(app_id and app_secret),
        "em_cache": bool(_cache["token"]),
        "expira_em_min": round(restante / 60, 1),
        "token_fixo": bool(os.getenv("ML_ACCESS_TOKEN", "").strip()),
    }
