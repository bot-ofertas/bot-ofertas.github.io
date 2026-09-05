# -*- coding: utf-8 -*-
"""
Provedor de links de afiliado da Amazon Brasil.
Stub — implementação futura via Amazon Associates / tag de afiliado.
"""
from __future__ import annotations

import os

from affiliates.base import AffiliateProvider


class AmazonAffiliateProvider(AffiliateProvider):
    name = "amazon"

    def can_handle(self, url: str) -> bool:
        return "amazon.com.br" in url or "amzn.to" in url

    def validate_affiliate_link(self, link: str) -> bool:
        """True só se a `tag=` de afiliado for query de verdade.

        A checagem antiga era substring (`"tag=" in link`): passava com a tag
        presa dentro de um `#fragment` (inerte, nunca chega no servidor da
        Amazon) e até com um `ascsubtag=` qualquer, que contém "tag=" como
        substring mas não é a Associate Tag — mesma classe de falha silenciosa
        do bug do Mercado Livre de 2026-08-04 (Regras 4 e 11).
        """
        if not link:
            return False
        if "amzn.to/" in link:
            return True
        from urllib.parse import urlsplit, parse_qs  # noqa: PLC0415
        tag = (parse_qs(urlsplit(link).query).get("tag") or [""])[0].strip()
        if not tag:
            return False
        esperada = os.getenv("AMAZON_AFFILIATE_TAG", "").strip()
        return tag == esperada if esperada else True

    def health_check(self) -> bool:
        return False  # não implementado

    def generate_affiliate_link(self, url: str) -> str | None:
        # TODO: usar Amazon Associates API com tag de afiliado
        # Por enquanto, não publicar sem link oficial
        return None
