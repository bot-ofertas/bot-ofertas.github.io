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
from datetime import datetime, timedelta

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
# `MLU\d{6,15}` e o Magalu (ver rastreador_magalu._id_magalu). Nao colide
# com `MLBU\d+` do ML: aquele exige o "B" na terceira letra.
_ID_NO_NOME = re.compile(r"-(MLBU?\d+|MLU\d{6,15}|B[A-Z0-9]{9})\.html$")

# Reler a pasta a cada checagem seria um listdir por produto avaliado. O
# conteudo so muda quando alguem publica, entao um cache curto basta — e
# curto de proposito: numa rodada longa o outro publicador pode ter
# commitado no meio.
_CACHE_S = 120.0
_cache: "tuple[float, dict[str, str]] | None" = None


# Ter pagina no site NAO bloqueia o produto para sempre. `limpar_antigos()`
# apaga a linha de `produtos` em 2 dias exatamente para permitir repostar "o
# mesmo produto com oferta diferente" (docstring de core/database.py). O que
# nunca existiu foi a segunda metade dessa frase: nada comparava a OFERTA.
# Medido em 2026-09-26: o volante Logitech G29 (MLB22307702) saiu em 04/09,
# 13/09 e 26/09 pelo MESMO preco (R$ 1599,00, 20% OFF) — tres vezes a mesma
# oferta no grupo. E sao 2495 paginas em docs/ofertas/: bloquear todas para
# sempre seria o erro oposto, tirar de rotacao produto que hoje esta 40%
# mais barato.
#
# Os dois dados necessarios ja estao na propria pagina, gerados sempre
# (core/blog_generator.py): o preco em `product:price:amount` e a data em
# "verificada em <strong>dd/mm/aaaa". Sem rede, sem banco, sem git — le
# so a pagina do candidato, nao as 2495.
_DIAS_MINIMOS_REPOST = float(os.getenv("DIAS_MINIMOS_REPOST") or 2)
_QUEDA_MINIMA_REPOST_PCT = float(os.getenv("QUEDA_MINIMA_REPOST_PCT") or 5)

_PRECO_NA_PAGINA = re.compile(
    r'property="product:price:amount"\s+content="([0-9]+(?:\.[0-9]+)?)"')
# Sem o "as" acentuado de proposito: a data basta e a regex fica imune a
# qualquer surpresa de encoding na leitura da pagina.
_DATA_NA_PAGINA = re.compile(r"verificada em <strong>(\d{2})/(\d{2})/(\d{4})")


def _mapa() -> dict[str, str]:
    """{ID oficial: nome do arquivo} das paginas ja publicadas.

    Devolve dict vazio quando a pasta nao existe ou nao da para ler —
    "nao consegui olhar" nunca pode virar "ja foi publicado", senao uma
    leitura falha calaria o bot inteiro (mesmo principio do psutil no
    supervisor e do checkout raso na Regra 16).
    """
    global _cache
    agora = time.time()
    if _cache is not None and (agora - _cache[0]) < _CACHE_S:
        return _cache[1]

    achados: dict[str, str] = {}
    try:
        for nome in os.listdir(_PASTA):
            m = _ID_NO_NOME.search(nome)
            if m:
                achados[m.group(1)] = nome
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("nao consegui ler %s (%s) — seguindo so com o banco local",
                    _PASTA, e)

    _cache = (agora, achados)
    return _cache[1]


def ids_publicados() -> frozenset[str]:
    """IDs de anuncio ja publicados por QUALQUER publicador, segundo o repo."""
    return frozenset(_mapa())


# `core/blog_generator.py:65` monta o nome do arquivo com `pid[:12]`: um ID
# mais longo que 12 chars chega TRUNCADO na pasta. Medido em 26/09/2026:
# `MLBU2939238991` (14) virou `...-MLBU29392389.html`, o registro procurava o
# ID inteiro, nunca achava, e o monitor portatil foi republicado no grupo com
# 4,2% de queda — abaixo do minimo, justamente o caso que a checagem existe
# para barrar. Sao 639 paginas com ID de exatamente 12 chars, todas nessa
# situacao. Cortar aqui em vez de mudar o nome do arquivo e de proposito:
# trocar `pid[:12]` mudaria a URL das 2495 paginas ja indexadas.
_TRUNCADO = 12


def _id_oficial(produto_id: str) -> str | None:
    """O ID oficial deste produto que JA aparece nas paginas, ou None."""
    if not produto_id:
        return None
    publicados = _mapa()
    if produto_id in publicados:
        return produto_id
    # O id do scraper pode ser "MLB123..." ou trazer o codigo dentro de um
    # slug maior; procura o codigo oficial dentro dele.
    codigos = [m.group(1) for m in
               re.finditer(r"(MLBU?\d+|MLU\d{6,15}|B[A-Z0-9]{9})", produto_id.upper())]
    for codigo in codigos:
        if codigo in publicados:
            return codigo
    # Nada bateu inteiro: tenta o ID como o nome do arquivo o guarda, cortado.
    # Uma colisao aqui exigiria dois anuncios com os mesmos 12 primeiros chars
    # do ID oficial; se acontecer, o custo e pular uma oferta boa, e nao mandar
    # a mesma duas vezes no grupo (Regra 11, o erro mais caro dos dois).
    for codigo in codigos:
        if len(codigo) > _TRUNCADO:
            curto = codigo[:_TRUNCADO]
            if curto in publicados:
                return curto
    return None


def oferta_publicada(produto_id: str) -> "tuple[float, datetime] | None":
    """(preco, quando) da ultima vez que este produto virou pagina no site.

    None quando o produto nao tem pagina, ou quando a pagina existe mas nao
    da para extrair os dois dados. Quem decide o repost trata esse None como
    "nao sei" e mantem o bloqueio: aqui o custo dos dois erros e o da Regra
    16 invertido em escala pequena — deixar passar republica a MESMA oferta
    no grupo (Regra 11), bloquear custa uma oferta que volta na rodada
    seguinte.
    """
    nome = None
    alvo = _id_oficial(produto_id)
    if alvo:
        nome = _mapa().get(alvo)
    if not nome:
        return None
    try:
        with open(os.path.join(_PASTA, nome), encoding="utf-8", errors="replace") as fh:
            html = fh.read()
    except OSError as e:
        log.warning("nao consegui ler a pagina %s (%s)", nome, e)
        return None

    mp = _PRECO_NA_PAGINA.search(html)
    md = _DATA_NA_PAGINA.search(html)
    if not mp or not md:
        return None
    try:
        preco = float(mp.group(1))
        quando = datetime(int(md.group(3)), int(md.group(2)), int(md.group(1)))
    except (ValueError, OverflowError):
        return None
    if preco <= 0:
        return None
    return preco, quando


def ja_publicado(produto_id: str, preco: "float | None" = None) -> bool:
    """True se este ID ja aparece nas paginas do site.

    `produto_id` pode vir com prefixo/sufixo do scraper; a comparacao usa o
    ID oficial embutido nele (MLB/MLBU do Mercado Livre, ASIN da Amazon,
    MLU+codigo do Magalu), que e o mesmo que o nome do arquivo carrega
    (Regra 11).

    Com `preco` informado, um produto ja publicado deixa de ser duplicata
    quando a oferta melhorou de verdade: passaram `DIAS_MINIMOS_REPOST` (2,
    o mesmo prazo de `limpar_antigos`) E o preco caiu pelo menos
    `QUEDA_MINIMA_REPOST_PCT` (5%) em relacao ao publicado. Sem `preco`, o
    comportamento e o de antes: ja tem pagina, e duplicata.
    """
    alvo = _id_oficial(produto_id)
    if alvo is None:
        return False
    if preco is None:
        return True
    try:
        preco = float(preco)
    except (TypeError, ValueError):
        return True
    if preco <= 0:
        return True

    anterior = oferta_publicada(alvo)
    if anterior is None:
        return True          # pagina ilegivel: "nao sei" mantem o bloqueio
    preco_pub, quando = anterior

    if datetime.now() - quando < timedelta(days=_DIAS_MINIMOS_REPOST):
        return True
    teto = preco_pub * (1.0 - _QUEDA_MINIMA_REPOST_PCT / 100.0)
    if preco > teto:
        return True

    queda = (1.0 - preco / preco_pub) * 100.0
    log.info("repost liberado: %s R$ %.2f -> R$ %.2f (-%.1f%%), publicado em %s",
             alvo, preco_pub, preco, queda, quando.strftime("%d/%m/%Y"))
    try:
        from core.metrics import inc  # noqa: PLC0415
        inc("repost_por_queda_de_preco")
    except Exception:
        pass
    return False
