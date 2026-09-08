# -*- coding: utf-8 -*-
"""
REDAÇÃO DE SEGREDOS EM LOG
==========================
Motivo (achado real, 2026-09-07): `monitor.log` e `rastreador.log` estavam
versionados neste repositório — que é público — com o token do bot do
Telegram em texto puro. Dois caminhos independentes colocaram o token lá:

  1. `monitor.py` gravava `str(e)` da exceção da python-telegram-bot, e essa
     mensagem é literalmente "The token `<token>` was rejected by the server".
  2. O logger do `httpx` (usado pela python-telegram-bot) registra a URL
     inteira de cada requisição — e no Telegram o token faz parte da URL:
     `https://api.telegram.org/bot<TOKEN>/sendPhoto`. Foram 20 linhas assim.

Nenhum dos dois é "alguém escreveu o token no log de propósito" — por isso
tapar um buraco só não resolve. A defesa fica aqui, num lugar por onde todo
log passa: um filtro que apaga o segredo do texto antes dele chegar ao
arquivo, e uma função para quem monta texto fora do logging.

Não substitui rotacionar a credencial já exposta: o que foi commitado
continua no histórico do git para sempre. Isto impede o PRÓXIMO vazamento.
"""
from __future__ import annotations

import logging
import os
import re

MASCARA = "***REDIGIDO***"

# Tokens que têm formato reconhecível são apagados mesmo que não estejam no
# ambiente deste processo — é o caso do token velho que já estava no log
# commitado, e de um token de outra instalação aparecendo num traceback.
_PADROES = (
    re.compile(r"\d{8,12}:AA[\w-]{30,}"),                 # bot do Telegram
    re.compile(r"sk-ant-api\d{2}-[\w-]{20,}"),            # Anthropic
    re.compile(r"APP_USR-[\w-]{20,}"),                    # Mercado Livre
    re.compile(r"TG-[\w-]{20,}"),                         # Mercado Livre (refresh)
)

# Variáveis de ambiente cujo VALOR nunca pode aparecer em log. Casadas pelo
# nome porque a lista cresce (Evolution, n8n, Instagram...) e esquecer de
# adicionar uma aqui é justamente como o token do Telegram vazou.
_NOMES_SENSIVEIS = re.compile(
    r"(TOKEN|SECRET|SENHA|PASSWORD|PASSWD|APIKEY|API_KEY|_KEY|CHAVE|ASSINATURA)",
    re.IGNORECASE,
)

# Valores curtos demais não são segredo e apagá-los estraga o log: um
# `PAPEL=nuvem` ou um `DEBUG=1` viraria máscara em toda linha que os cita.
_TAMANHO_MINIMO = 12


def _valores_do_ambiente() -> list[str]:
    achados = []
    for nome, valor in os.environ.items():
        if not valor or len(valor) < _TAMANHO_MINIMO:
            continue
        if _NOMES_SENSIVEIS.search(nome):
            achados.append(valor)
    # Do mais longo para o mais curto: se um segredo contém o outro (um
    # token e o seu prefixo, por exemplo), o maior tem de ser apagado antes,
    # senão sobra a cauda do maior no texto.
    return sorted(set(achados), key=len, reverse=True)


def redigir(texto: str) -> str:
    """Devolve `texto` sem segredos. Seguro para qualquer entrada."""
    if not texto:
        return texto
    try:
        for valor in _valores_do_ambiente():
            if valor in texto:
                texto = texto.replace(valor, MASCARA)
        for padrao in _PADROES:
            texto = padrao.sub(MASCARA, texto)
    except Exception:
        # Redigir nunca pode derrubar quem está logando — mas também não
        # posso devolver o texto original, que é justamente o que pode ter
        # o segredo. Na dúvida, o log perde a linha, não o sigilo.
        return MASCARA
    return texto


class FiltroDeSegredos(logging.Filter):
    """Apaga segredos da mensagem antes dela chegar a qualquer handler.

    Age sobre `record.msg` e `record.args` porque o logging só junta os dois
    na hora de formatar: o token do httpx chega como argumento (`%s`), não
    dentro da mensagem — filtrar só `msg` deixaria passar exatamente o caso
    que originou este arquivo.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redigir(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redigir(v) if isinstance(v, str) else v
                               for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(redigir(a) if isinstance(a, str) else a
                                    for a in record.args)
        return True
