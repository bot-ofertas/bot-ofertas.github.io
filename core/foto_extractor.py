# -*- coding: utf-8 -*-
"""
FOTO EXTRACTOR — busca a foto de um produto ML por múltiplas rotas.

Ordem de tentativas (rápido → lento):
  1. og:image via GET simples com user-agent real
  2. HTML dumps procurando D_NQ_NP no CDN mlstatic
  3. Playwright headless clicando na página (última opção)
  4. Reverse image do próprio nome do produto via Google Images fallback
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

log = logging.getLogger("foto_extractor")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}


def _via_html(url_produto: str) -> Optional[str]:
    try:
        r = requests.get(url_produto, headers=_HEADERS, timeout=15, allow_redirects=True)
        html = r.text
        # og:image
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I,
        )
        if m:
            u = m.group(1)
            if u.startswith("http") and "logo" not in u.lower():
                return u.replace("http://", "https://").replace("-I.", "-O.")
        # D_NQ_NP no HTML
        for pat in [
            r'https://http2\.mlstatic\.com/D_NQ_NP_2X_[^"\'\s>]+',
            r'https://http2\.mlstatic\.com/D_NQ_NP_[^"\'\s>]+',
        ]:
            m2 = re.search(pat, html)
            if m2:
                return m2.group(0).replace("-I.", "-O.")
    except Exception as e:
        log.debug("via_html falhou: %s", e)
    return None


def _via_search_api(nome_produto: str) -> Optional[str]:
    """Busca a foto via search público do Mercado Livre pelo nome."""
    try:
        # Search API público — sem auth
        q = " ".join(nome_produto.split()[:5])  # 5 primeiras palavras
        r = requests.get(
            "https://api.mercadolibre.com/sites/MLB/search",
            params={"q": q, "limit": 3},
            headers=_HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return None
        for item in r.json().get("results", []):
            thumb = item.get("thumbnail") or ""
            if thumb and "D_NQ_NP" in thumb:
                return thumb.replace("-I.", "-O.").replace("http://", "https://")
    except Exception as e:
        log.debug("via_search_api falhou: %s", e)
    return None


def _via_playwright(url_produto: str) -> Optional[str]:
    """Última opção: abre a página em headless e extrai."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            ctx = b.new_context(
                user_agent=_HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            page.goto(url_produto, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(2500)
            foto = page.evaluate("""() => {
                const og = document.querySelector('meta[property="og:image"]');
                if (og) {
                    const u = og.getAttribute('content') || '';
                    if (u.includes('D_NQ_NP')) return u.replace('-I.','-O.');
                }
                const im = [...document.querySelectorAll('img')]
                    .map(i => i.getAttribute('data-zoom') || i.src)
                    .find(u => u && u.includes('D_NQ_NP') && !u.includes('logo'));
                return im ? im.replace('-I.','-O.') : '';
            }""")
            b.close()
            return foto or None
    except Exception as e:
        log.debug("via_playwright falhou: %s", e)
    return None


def extrair_foto(url_produto: str, titulo: str = "") -> str:
    """Retorna a URL da foto do produto ML ou string vazia se não achou.

    Testa em ordem: HTML → search API por nome → Playwright.
    """
    for tentativa in (_via_html, _via_playwright):
        foto = tentativa(url_produto)
        if foto:
            log.info("Foto encontrada via %s", tentativa.__name__)
            return foto
    if titulo:
        foto = _via_search_api(titulo)
        if foto:
            log.info("Foto encontrada via search API pelo título")
            return foto
    return ""
