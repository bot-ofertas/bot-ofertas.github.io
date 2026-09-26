# -*- coding: utf-8 -*-
"""Magazine Luiza — links de afiliado via Magazine Você (Parceiro Magalu).

Como o programa funciona (e por que NAO existe chamada de API aqui): o
Parceiro Magalu nao devolve um link encurtado por requisicao como o
Mercado Livre. Ele te da uma VITRINE — uma loja espelho do catalogo inteiro
em `magazinevoce.com.br/<vitrine>/`. Qualquer produto do
`magazineluiza.com.br` vira venda sua quando o mesmo caminho e servido sob
a sua vitrine. Ou seja: o link de afiliado e uma reescrita de caminho
deterministica, nao uma credencial de API.

    https://www.magazineluiza.com.br/geladeira-brastemp/p/123456700/ED/REFR/
    https://www.magazinevoce.com.br/magazineexemplo/geladeira-brastemp/p/123456700/ED/REFR/
                                    ^^^^^^^^^^^^^^^ a vitrine entra aqui

O que NAO da para descobrir no codigo e o nome da vitrine do Daniel — ele
sai do painel do Parceiro Magalu. Sem `MAGALU_VITRINE` no `.env` este
provedor se declara inativo (`health_check() -> False`) e devolve `None`,
e a Regra 7 cuida do resto: sem link de afiliado valido, nao publica. O
que nao acontece aqui, por decisao explicita, e inventar uma vitrine para
o fluxo "passar" — isso publicaria a oferta com a comissao indo para
ninguem (ou para outra pessoa), que e pior do que nao publicar.

Regra 11: toda manipulacao de URL passa por `urllib.parse`. O
`#fragment` e descartado antes de qualquer coisa — foi um fragmento
sobrevivente que quebrou o afiliado do ML em 2026-08-04 (commit ed84736),
e aqui o estrago seria o mesmo: a vitrine entra no caminho, mas o produto
que o navegador abre vem do fragmento.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit, urlunsplit

from affiliates.base import AffiliateProvider

_DOMINIO_VITRINE = "www.magazinevoce.com.br"
_HOSTS_LOJA = ("magazineluiza.com.br", "magazinevoce.com.br")

# Vitrines validas sao `magazine` + letras/numeros. O painel gera sempre
# nesse formato; validar aqui evita que um valor colado errado no .env
# (uma URL inteira, um espaco, um nome com acento) vire um link quebrado
# publicado no grupo.
_VITRINE_OK = re.compile(r"^magazine[a-z0-9]{2,40}$")

# Codigo do produto no catalogo Magalu: `/p/<codigo>/...`. E o ID oficial
# do anuncio — a Regra 11 manda preferir ele a derivar da URL/slug.
_CODIGO = re.compile(r"/p/([0-9]{6,15})(?:/|$)")


def vitrine() -> str:
    """Nome da vitrine configurada, normalizado. String vazia se ausente.

    Aceita as tres formas que aparecem no painel e no copy-paste do
    Daniel — `magazineexemplo`, `exemplo` e a URL completa da vitrine — e
    devolve sempre a forma canonica.
    """
    bruto = (os.getenv("MAGALU_VITRINE") or "").strip()
    if not bruto:
        return ""
    # URL completa colada do painel: fica so com o primeiro segmento.
    if "://" in bruto or "magazinevoce.com.br" in bruto:
        caminho = urlsplit(bruto if "://" in bruto else f"https://{bruto}").path
        partes = [p for p in caminho.split("/") if p]
        bruto = partes[0] if partes else ""
    bruto = bruto.strip("/").lower()
    if not bruto:
        return ""
    if not bruto.startswith("magazine"):
        bruto = f"magazine{bruto}"
    return bruto if _VITRINE_OK.match(bruto) else ""


def magalu_ativo() -> bool:
    """True quando ha vitrine valida — mesma forma de `amazon_ativo()`."""
    return bool(vitrine())


def codigo_produto(url: str) -> str | None:
    """Codigo oficial do anuncio, para deduplicacao estavel (Regra 11)."""
    try:
        caminho = urlsplit(url).path
    except Exception:
        return None
    achado = _CODIGO.search(caminho)
    return achado.group(1) if achado else None


class MagaluAffiliateProvider(AffiliateProvider):
    name = "magalu"

    def can_handle(self, url: str) -> bool:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except Exception:
            return False
        return any(host == d or host.endswith("." + d) for d in _HOSTS_LOJA)

    def generate_affiliate_link(self, url: str) -> str | None:
        alvo = vitrine()
        if not alvo or not self.can_handle(url):
            return None

        try:
            partes = urlsplit(url)
        except Exception:
            return None

        # Regra 11: fragmento fora ANTES de tocar no caminho. `urlsplit` ja
        # separa o fragmento, entao basta nao devolve-lo no `urlunsplit`.
        segmentos = [s for s in partes.path.split("/") if s]
        host = (partes.hostname or "").lower()

        if host.endswith("magazinevoce.com.br"):
            # Ja e uma vitrine. Se for de OUTRO parceiro, troca pela nossa —
            # republicar o link de outra pessoa manda a comissao para ela.
            if segmentos and segmentos[0].lower().startswith("magazine"):
                segmentos = segmentos[1:]

        if not segmentos:
            return None

        caminho = "/" + "/".join([alvo] + segmentos)
        # O catalogo do Magalu serve os produtos com barra final; sem ela a
        # loja responde com um redirect que descarta a query de origem.
        if partes.path.endswith("/"):
            caminho += "/"

        return urlunsplit(("https", _DOMINIO_VITRINE, caminho, partes.query, ""))

    def validate_affiliate_link(self, link: str) -> bool:
        """Confere que a vitrine chegou como PRIMEIRO segmento do caminho.

        Nao e checagem por substring (Regra 3/4/11): `"magazineexemplo" in
        link` aprovaria tambem `.../outravitrine/magazineexemplo-algo/`,
        que nao paga comissao nenhuma.
        """
        alvo = vitrine()
        if not link or not alvo:
            return False
        try:
            partes = urlsplit(link)
        except Exception:
            return False
        host = (partes.hostname or "").lower()
        if not host.endswith("magazinevoce.com.br"):
            return False
        segmentos = [s for s in partes.path.split("/") if s]
        # Precisa da vitrine E de um produto abaixo dela: a vitrine sozinha
        # e a home da loja, nao a oferta que foi anunciada.
        return len(segmentos) >= 2 and segmentos[0].lower() == alvo

    def health_check(self) -> bool:
        return magalu_ativo()
