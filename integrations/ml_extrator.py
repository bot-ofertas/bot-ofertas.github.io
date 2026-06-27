# -*- coding: utf-8 -*-
"""
Extrator de dados de produtos do Mercado Livre a partir de URL.
Lê os metadados estruturados (JSON-LD e Open Graph) que o próprio ML
disponibiliza publicamente para crawlers (Google, WhatsApp, Telegram).

Funciona com links curtos de afiliado (meli.la/...) e URLs completas.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import urllib.parse

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}
_TIMEOUT = 20


def _get(url: str) -> tuple[str, str]:
    """Faz GET seguindo redirects e retorna (html, url_final)."""
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace"), resp.url


def _extrair_json_ld(html: str) -> dict:
    """Extrai o primeiro JSON-LD do tipo Product."""
    blocos = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    )
    for bloco in blocos:
        try:
            dados = json.loads(bloco)
            if isinstance(dados, list):
                dados = next((d for d in dados if d.get("@type") == "Product"), {})
            if dados.get("@type") == "Product":
                return dados
        except (json.JSONDecodeError, StopIteration):
            continue
    return {}


def _meta(html: str, prop: str) -> str:
    m = re.search(
        rf'<meta[^>]+(?:property|name)=["\'](?:og:)?{re.escape(prop)}["\'][^>]*content=["\']([^"\']+)["\']',
        html, re.IGNORECASE
    )
    if not m:
        m = re.search(
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:)?{re.escape(prop)}["\']',
            html, re.IGNORECASE
        )
    return m.group(1) if m else ""


def extrair(url: str) -> dict | None:
    """
    Extrai dados de um produto a partir de uma URL do ML (incluindo links de afiliado).
    Retorna dict com: titulo, preco, preco_original, foto, link
    Retorna None se não conseguir extrair dados úteis.
    """
    try:
        html, url_final = _get(url)
    except Exception as e:
        raise RuntimeError(f"Não foi possível acessar a URL: {e}") from e

    # Se redirecionou para página social do ML, busca a URL canônica do produto
    if "/social/" in url_final or "forceInApp" in url_final:
        m_canon = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', html)
        m_og    = re.search(r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']', html)
        produto_url = (m_canon or m_og)
        if produto_url:
            try:
                html2, url_final2 = _get(produto_url.group(1))
                html, url_final = html2, url_final2
            except Exception:
                pass

    # 1. JSON-LD (mais confiável — dado estruturado oficial do ML)
    ld = _extrair_json_ld(html)
    titulo = ld.get("name", "")
    foto   = ""
    if isinstance(ld.get("image"), list):
        foto = ld["image"][0] if ld["image"] else ""
    elif isinstance(ld.get("image"), str):
        foto = ld["image"]

    oferta = ld.get("offers", {})
    if isinstance(oferta, list):
        oferta = oferta[0] if oferta else {}
    preco = None
    try:
        preco = float(oferta.get("price", 0)) or None
    except (ValueError, TypeError):
        pass

    # 2. Fallback: Open Graph / meta tags
    if not titulo:
        titulo = _meta(html, "title")
    if not foto:
        foto = _meta(html, "image")

    # 3. Preço original (meta tag específica do ML)
    preco_original = None
    m_orig = re.search(r'"original_price"\s*:\s*([\d.]+)', html)
    if m_orig:
        try:
            preco_original = float(m_orig.group(1))
        except ValueError:
            pass

    # 4. Se não extraiu preço via JSON-LD, tenta padrões conhecidos do ML
    if not preco:
        for padrao in [
            r'"price"\s*:\s*([\d.]+)',
            r'"sale_price"\s*:\s*([\d.]+)',
            r'itemprop="price"\s+content="([\d.]+)"',
            r'"amount"\s*:\s*([\d.]+)',
            r'andes-money-amount__fraction[^>]*>([\d.]+)<',
        ]:
            m = re.search(padrao, html)
            if m:
                try:
                    preco = float(m.group(1).replace(".", "").replace(",", "."))
                    if preco > 1:  # ignora valores absurdamente baixos
                        break
                    preco = None
                except ValueError:
                    pass

    # 5. Preço original — mais padrões
    if not preco_original:
        for padrao in [
            r'"original_price"\s*:\s*([\d.]+)',
            r'"base_price"\s*:\s*([\d.]+)',
            r'andes-money-amount--previous[^>]*>.*?<span[^>]*>([\d.]+)<',
        ]:
            m = re.search(padrao, html, re.DOTALL)
            if m:
                try:
                    val = float(m.group(1).replace(".", "").replace(",", "."))
                    if val > 1:
                        preco_original = val
                        break
                except ValueError:
                    pass

    if not titulo:
        return None

    return {
        "titulo":          titulo.strip(),
        "preco":           preco,
        "preco_original":  preco_original if preco_original and preco and preco_original > preco else None,
        "foto":            foto or None,
        "link":            url_final,
    }


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else input("Cole a URL do produto: ").strip()
    resultado = extrair(url)
    if resultado:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    else:
        print("Não foi possível extrair dados desta URL.")
