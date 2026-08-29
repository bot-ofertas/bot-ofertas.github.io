# -*- coding: utf-8 -*-
"""
NET — camada HTTP resiliente compartilhada (retry, backoff e tolerância a
queda de DNS).

Motivo (evidência real, relatório de 26/08/2026):
    [2026-08-25 23:20:32] rodada_falhou — "Timed out"
e, nos logs do rastreador, falhas intermitentes de resolução de nome
(`getaddrinfo failed` / `Temporary failure in name resolution`) durante a
madrugada. Cada chamada `requests.get(...)` espalhada pelo projeto tratava
isso como erro definitivo e derrubava a operação inteira, quando na prática
o link volta em segundos.

Este módulo centraliza:
  - retry com backoff exponencial + jitter (nunca todos os processos
    retentando no mesmo instante);
  - distinção entre falha transitória (DNS/timeout/5xx/429) e definitiva
    (404/403), pra não insistir no que nunca vai dar certo;
  - cabeçalhos de navegador reais — vários CDNs (inclusive o `mlstatic` das
    fotos do Mercado Livre) devolvem 403 para o User-Agent padrão do
    `requests`.

Uso:
    from core.net import get, baixar_bytes
    r = get("https://api.mercadolibre.com/...", timeout=10)
    dados = baixar_bytes("https://http2.mlstatic.com/....jpg")
"""
from __future__ import annotations

import logging
import random
import socket
import threading
import time
from typing import Iterable

import requests

log = logging.getLogger("net")

TIMEOUT_PADRAO = 15
TENTATIVAS_PADRAO = 3

HEADERS_NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.mercadolivre.com.br/",
}

# Erros que valem uma nova tentativa: rede instável, DNS fora do ar,
# servidor sobrecarregado. 4xx (exceto 429) não entram — insistir num 404
# só gasta tempo da rodada.
_STATUS_RETENTAVEIS = frozenset({408, 425, 429, 500, 502, 503, 504})


class FalhaDeRede(RuntimeError):
    """Erro de rede depois de esgotadas as tentativas."""


def dns_ok(host: str = "api.mercadolibre.com", timeout: float = 3.0) -> bool:
    """True se o DNS resolve `host` agora.

    Usado como pré-checagem barata antes de uma rodada inteira: sem rede,
    é melhor registrar "sem DNS" e sair em `timeout` segundos do que gastar
    minutos em timeouts encadeados e terminar com um genérico "Timed out".

    A resolução roda numa thread própria porque `getaddrinfo` é uma chamada
    bloqueante do sistema: ela NÃO obedece `socket.setdefaulttimeout()` (que
    só vale para sockets criados depois). A versão anterior usava justamente
    isso — o limite de 3s não existia na prática (um DNS que não responde
    segurava a rodada pelo timeout do resolvedor do sistema, tipicamente
    ~40s) e, de quebra, mexia num ajuste GLOBAL do processo, valendo para as
    threads do healthcheck e dos rastreadores durante a janela.
    """
    resultado: dict = {}

    def _resolver() -> None:
        try:
            socket.getaddrinfo(host, 443)
            resultado["ok"] = True
        except OSError as e:
            resultado["ok"] = False
            resultado["erro"] = e

    # daemon: se o resolvedor do sistema travar de vez, a thread pendurada
    # não pode impedir o bot de encerrar.
    t = threading.Thread(target=_resolver, name="dns-check", daemon=True)
    t.start()
    t.join(timeout)

    if "ok" not in resultado:
        log.warning("DNS não respondeu para %s em %.1fs", host, timeout)
        return False
    if not resultado["ok"]:
        log.warning("DNS indisponível para %s: %s", host, resultado.get("erro"))
        return False
    return True


def _espera(tentativa: int, base: float) -> float:
    """Backoff exponencial com jitter (±25%)."""
    bruto = base * (2 ** (tentativa - 1))
    return min(bruto * random.uniform(0.75, 1.25), 30.0)


def request(
    metodo: str,
    url: str,
    *,
    tentativas: int = TENTATIVAS_PADRAO,
    timeout: float = TIMEOUT_PADRAO,
    backoff: float = 1.5,
    headers: dict | None = None,
    status_retentaveis: Iterable[int] = _STATUS_RETENTAVEIS,
    **kwargs,
) -> requests.Response:
    """`requests.request` com retry/backoff. Levanta FalhaDeRede no fim.

    Devolve a resposta mesmo com status 4xx não-retentável — quem chama
    decide o que fazer (o código de status é informação útil, não erro de
    rede).
    """
    hdrs = dict(HEADERS_NAVEGADOR)
    if headers:
        hdrs.update(headers)
    retentaveis = frozenset(status_retentaveis)
    ultimo_erro: Exception | None = None

    for tentativa in range(1, max(1, tentativas) + 1):
        try:
            resp = requests.request(metodo, url, timeout=timeout, headers=hdrs, **kwargs)
            if resp.status_code in retentaveis and tentativa < tentativas:
                espera = _espera(tentativa, backoff)
                log.warning(
                    "%s %s → HTTP %d (tentativa %d/%d) — nova tentativa em %.1fs",
                    metodo, url[:90], resp.status_code, tentativa, tentativas, espera,
                )
                time.sleep(espera)
                continue
            return resp
        except (requests.ConnectionError, requests.Timeout, socket.gaierror) as e:
            ultimo_erro = e
            if tentativa >= tentativas:
                break
            espera = _espera(tentativa, backoff)
            log.warning(
                "%s %s falhou (%s) — tentativa %d/%d, nova em %.1fs",
                metodo, url[:90], type(e).__name__, tentativa, tentativas, espera,
            )
            time.sleep(espera)

    raise FalhaDeRede(f"{metodo} {url[:120]} falhou após {tentativas} tentativa(s): {ultimo_erro}")


def get(url: str, **kwargs) -> requests.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs) -> requests.Response:
    return request("POST", url, **kwargs)


def baixar_bytes(url: str, *, tentativas: int = 2, timeout: float = 15,
                 max_bytes: int = 10 * 1024 * 1024) -> bytes | None:
    """Baixa o conteúdo de `url`. Devolve None (sem levantar) se não der.

    Loga o status HTTP quando o servidor responde mas recusa: sem isso, a
    falha de download da foto aparecia só como "falha ao publicar"
    genérico, sem nenhuma pista de que o problema era o CDN devolvendo 403.
    """
    try:
        r = get(url, tentativas=tentativas, timeout=timeout, allow_redirects=True)
    except FalhaDeRede as e:
        log.warning("Download falhou (rede): %s", e)
        return None
    if r.status_code != 200:
        log.warning("Download recusado: HTTP %d em %s", r.status_code, url[:110])
        return None
    conteudo = r.content
    if not conteudo:
        log.warning("Download vazio: %s", url[:110])
        return None
    if len(conteudo) > max_bytes:
        log.warning("Download grande demais (%d bytes): %s", len(conteudo), url[:110])
        return None
    return conteudo
