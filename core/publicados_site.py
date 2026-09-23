# -*- coding: utf-8 -*-
"""Registro COMPARTILHADO do que ja foi publicado, lido de `docs/ofertas/`.

Por que existe
--------------
Sao tres publicadores capazes de postar no MESMO canal (PC, GitHub Actions,
servidor), cada um com o seu proprio banco SQLite de deduplicacao. Nenhum
enxerga o que o outro publicou — e `_e_duplicata()` consultava so o banco
local. Quando dois deles acham a mesma oferta quente, ela sai DUAS VEZES no
grupo. A Regra 16 nomeia esse risco; o que faltava era o registro comum.

O registro ja existia e ninguem lia: cada publicacao cria
`docs/ofertas/<slug>-<ID>.html`, e esse arquivo e commitado no repositorio.
O ID oficial do anuncio (MLB.../ASIN) esta no proprio NOME do arquivo, entao
basta listar a pasta — sem rede, sem banco, sem depender de o outro
publicador estar de pe.

Limites, ditos aqui para nao serem descobertos como surpresa
------------------------------------------------------------
- So enxerga o que chegou ao checkout DESTA maquina. Um publicador que ainda
  nao empurrou (ou cujo push falha) permanece invisivel — por isso
  `core/site_publisher.py` passou a recusar o ramo errado em vez de fingir
  que publicou.
- Um checkout raso ou desatualizado enxerga menos. Isso reduz a protecao,
  nunca inverte: na duvida o caminho e o banco local, que e o que ja valia.
- Nao substitui a deduplicacao local. E uma checagem A MAIS.
"""
from __future__ import annotations

import logging
import os
import re
import time

log = logging.getLogger("publicados_site")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PASTA = os.path.join(_BASE, "docs", "ofertas")

# O ID fica no fim do nome, antes do .html: "...-MLB54067366.html",
# "...-B09FKWS793.html", "...-MLBU77700863.html".
#
# SO os formatos oficiais (Regra 11: usar o ID do anuncio, nao derivar do
# slug). Uma regex frouxa como `-([A-Z0-9]{8,})\.html$` casa tambem com
# slug que termina em numero — medido nesta pasta, ela colhia "22099816" e
# "6555005904", que nao sao IDs de produto. Falso positivo aqui e pior do
# que nao ter a checagem: faria o bot PULAR uma oferta boa achando que ja
# publicou, e em silencio.
_ID_NO_NOME = re.compile(r"-(MLBU?\d+|B[A-Z0-9]{9})\.html$")

# Reler a pasta a cada checagem seria um listdir por produto avaliado. O
# conteudo so muda quando alguem publica, entao um cache curto basta — e
# curto de proposito: numa rodada longa o outro publicador pode ter
# commitado no meio.
_CACHE_S = 120.0
_cache: "tuple[float, frozenset[str]] | None" = None


def ids_publicados() -> frozenset[str]:
    """IDs de anuncio ja publicados por QUALQUER publicador, segundo o repo.

    Devolve conjunto vazio quando a pasta nao existe ou nao da para ler —
    "nao consegui olhar" nunca pode virar "ja foi publicado", senao uma
    leitura falha calaria o bot inteiro (mesmo principio do psutil no
    supervisor e do checkout raso na Regra 16).
    """
    global _cache
    agora = time.time()
    if _cache is not None and (agora - _cache[0]) < _CACHE_S:
        return _cache[1]

    ids: set[str] = set()
    try:
        for nome in os.listdir(_PASTA):
            m = _ID_NO_NOME.search(nome)
            if m:
                ids.add(m.group(1))
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("nao consegui ler %s (%s) — seguindo so com o banco local",
                    _PASTA, e)

    _cache = (agora, frozenset(ids))
    return _cache[1]


def ja_publicado(produto_id: str) -> bool:
    """True se este ID ja aparece nas paginas do site.

    `produto_id` pode vir com prefixo/sufixo do scraper; a comparacao usa o
    ID oficial embutido nele (MLB/MLBU do Mercado Livre, ASIN da Amazon),
    que e o mesmo que o nome do arquivo carrega (Regra 11).
    """
    if not produto_id:
        return False
    publicados = ids_publicados()
    if produto_id in publicados:
        return True
    # O id do scraper pode ser "MLB123..." ou trazer o codigo dentro de um
    # slug maior; procura o codigo oficial dentro dele.
    for m in re.finditer(r"(MLBU?\d+|B[A-Z0-9]{9})", produto_id.upper()):
        if m.group(1) in publicados:
            return True
    return False
