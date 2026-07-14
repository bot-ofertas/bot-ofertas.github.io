# -*- coding: utf-8 -*-
"""
Scraper das páginas de ofertas do Mercado Livre usando Playwright (browser headless).
Renderiza o JavaScript do site e extrai produtos do JSON embutido (_n.ctx.r)
e também via DOM como fallback.

Funções principais:
  buscar_ofertas_browser_async(nicho) — async, para rastreador.py (asyncio)
  buscar_ofertas_browser(nicho)       — sync, para scripts standalone
"""
from __future__ import annotations

import json
import os
import re

_AFFILIATE_TOOL_ID = os.getenv("ML_AFFILIATE_TOOL_ID", "47114387")

_FILTROS_CATEGORIA: dict[str, str] = {
    # ── Celulares ───────────────────────────────────────────────────────────
    "celulares":        "https://www.mercadolivre.com.br/ofertas?category=MLB1051",

    # ── Informática — subcategorias ──────────────────────────────────────────
    "notebooks":        "https://www.mercadolivre.com.br/ofertas?category=MLB1652",
    "tablets":          "https://www.mercadolivre.com.br/ofertas?category=MLB1684",
    "informatica":      "https://www.mercadolivre.com.br/ofertas?category=MLB1648",  # periféricos / geral
    "monitores":        "https://www.mercadolivre.com.br/ofertas?category=MLB430235",
    "impressoras":      "https://www.mercadolivre.com.br/ofertas?category=MLB430237",
    "armazenamento":    "https://www.mercadolivre.com.br/ofertas?category=MLB430233",
    "redes":            "https://www.mercadolivre.com.br/ofertas?category=MLB430240",

    # ── Eletrônicos ─────────────────────────────────────────────────────────
    "eletronicos":      "https://www.mercadolivre.com.br/ofertas?category=MLB1000",
    "tvs":              "https://www.mercadolivre.com.br/ofertas?category=MLB1002",
    "audio":            "https://www.mercadolivre.com.br/ofertas?category=MLB1003",
    "cameras":          "https://www.mercadolivre.com.br/ofertas?category=MLB1015",

    # ── Casa e Eletrodomésticos ──────────────────────────────────────────────
    "casa":             "https://www.mercadolivre.com.br/ofertas?category=MLB1574",
    "eletrodomesticos": "https://www.mercadolivre.com.br/ofertas?category=MLB1580",
    "moveis":           "https://www.mercadolivre.com.br/ofertas?category=MLB1499",

    # ── Moda e Beleza ───────────────────────────────────────────────────────
    "moda":             "https://www.mercadolivre.com.br/ofertas?category=MLB1430",
    "beleza":           "https://www.mercadolivre.com.br/ofertas?category=MLB1246",
    "saude":            "https://www.mercadolivre.com.br/ofertas?category=MLB1300",

    # ── Esportes e Lazer ────────────────────────────────────────────────────
    "esportes":         "https://www.mercadolivre.com.br/ofertas?category=MLB1276",
    "games":            "https://www.mercadolivre.com.br/ofertas?category=MLB1144",
    "brinquedos":       "https://www.mercadolivre.com.br/ofertas?category=MLB1132",

    # ── Família ─────────────────────────────────────────────────────────────
    "bebes":            "https://www.mercadolivre.com.br/ofertas?category=MLB5726",
    "livros":           "https://www.mercadolivre.com.br/ofertas?category=MLB3025",

    # ── Veículos e Ferramentas ──────────────────────────────────────────────
    "automotivo":       "https://www.mercadolivre.com.br/ofertas?category=MLB1747",
    "ferramentas":      "https://www.mercadolivre.com.br/ofertas?category=MLB1039",

    # ── Animais ─────────────────────────────────────────────────────────────
    "pet":              "https://www.mercadolivre.com.br/ofertas?category=MLB1514",
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# Script JS executado no browser para extrair cards do DOM
_DOM_SCRIPT = """
() => {
    const resultado = [];
    const cards = Array.from(document.querySelectorAll(
        '.andes-card.poly-card, [class*="poly-card--grid"], [class*="poly-card--list"]'
    ));
    for (const card of cards.slice(0, 50)) {
        try {
            const linkEl = card.querySelector('a[href*="mercadolivre"]');
            if (!linkEl) continue;
            const link = linkEl.href;

            const tituloEl = card.querySelector('.poly-component__title, h2, h3, [class*="title"]');
            const titulo = tituloEl ? tituloEl.textContent.trim() : '';
            if (!titulo) continue;

            const precoEl = card.querySelector(
                '.poly-price__current .andes-money-amount__fraction,' +
                '.andes-money-amount--cents-superscript .andes-money-amount__fraction,' +
                '[class*="price__fraction"]'
            );
            const precoCents = card.querySelector(
                '.poly-price__current .andes-money-amount__cents, [class*="price__cents"]'
            );
            let precoTexto = precoEl ? precoEl.textContent.trim() : '';
            if (precoTexto && precoCents) precoTexto += ',' + precoCents.textContent.trim();

            const origEl = card.querySelector(
                '[class*="price--original"] .andes-money-amount__fraction,' +
                '.andes-money-amount--previous .andes-money-amount__fraction'
            );
            const origTexto = origEl ? origEl.textContent.trim() : '';

            const descEl = card.querySelector('[class*="discount"], [class*="badge__label"]');
            const descTexto = descEl ? descEl.textContent.trim() : '';

            const imgEl = card.querySelector('img');
            const fotoUrl = imgEl ? (imgEl.getAttribute('data-src') || imgEl.getAttribute('data-lazy') || imgEl.src || '') : '';
            const foto = fotoUrl.startsWith('http') ? fotoUrl : '';

            // Cupom de desconto adicional
            const cupomEl = card.querySelector(
                '[class*="coupon"], [class*="cupom"], .poly-coupons__discount, ' +
                '.andes-badge__content, [data-testid*="coupon"], [class*="badge--coupon"]'
            );
            const cupomTexto = cupomEl ? cupomEl.textContent.trim() : '';

            resultado.push({ titulo, link, precoTexto, origTexto, descTexto, foto, cupomTexto });
        } catch(e) {}
    }
    return resultado;
}
"""


# ── Funções de extração ────────────────────────────────────────────────────────

def _gerar_link_afiliado(url: str) -> str:
    url_limpa = url.split("?")[0]
    return (
        f"{url_limpa}"
        f"?matt_tool_id={_AFFILIATE_TOOL_ID}"
        f"&matt_word=oferta&matt_source=bot_telegram"
    )


def _extrair_preco_texto(texto: str) -> float | None:
    nums = re.sub(r"[^\d,]", "", texto.replace(".", ""))
    nums = nums.replace(",", ".")
    try:
        v = float(nums)
        return v if v > 1 else None
    except ValueError:
        return None


def _normalizar_dom(raw: list) -> list[dict]:
    """Normaliza resultado bruto do script DOM para formato padrão de produto."""
    produtos = []
    for item in raw or []:
        titulo = item.get("titulo", "").replace("�", "").strip()
        link = item.get("link", "").strip()
        if not titulo or not link or "mercadolivre.com.br/ofertas" in link:
            continue

        preco = _extrair_preco_texto(item.get("precoTexto", ""))
        preco_orig = _extrair_preco_texto(item.get("origTexto", ""))

        m_desc = re.search(r"(\d+)\s*%", item.get("descTexto", ""))
        if m_desc:
            desconto_pct = float(m_desc.group(1))
        elif preco and preco_orig and preco_orig > preco:
            desconto_pct = round((1 - preco / preco_orig) * 100, 1)
        else:
            desconto_pct = 0.0

        foto = item.get("foto", "")

        # Normaliza cupom: extrai percentual ou valor em reais
        cupom_raw = item.get("cupomTexto", "").strip()
        cupom = None
        if cupom_raw:
            cupom_raw_lower = cupom_raw.lower()
            if any(k in cupom_raw_lower for k in ("cupom", "coupon", "%")):
                cupom = cupom_raw

        produtos.append({
            "titulo":         titulo,
            "preco":          preco,
            "preco_original": preco_orig if preco_orig and preco and preco_orig > preco else None,
            "desconto_pct":   desconto_pct,
            "link":           link,
            "foto":           foto if foto and foto.startswith("http") else None,
            "cupom":          cupom,
        })
    return produtos


def _extrair_produtos_json(html: str) -> list[dict]:
    """Extrai produtos do JSON embutido pelo ML nas tags <script> (_n.ctx.r=...)."""
    produtos: list[dict] = []

    m = re.search(r'_n\.ctx\.r\s*=\s*(\{.+)', html, re.DOTALL)
    if not m:
        return []

    raw_json = m.group(1)
    end = raw_json.find("</script>")
    if end > 0:
        raw_json = raw_json[:end].rstrip("; \n\r\t")

    try:
        dados = json.loads(raw_json)
    except json.JSONDecodeError:
        for cutoff in range(len(raw_json) - 1, max(len(raw_json) - 500, 0), -1):
            if raw_json[cutoff] == '}':
                try:
                    dados = json.loads(raw_json[:cutoff + 1])
                    break
                except json.JSONDecodeError:
                    continue
        else:
            return []

    def _percorrer(obj, depth=0):
        if depth > 15:
            return
        if isinstance(obj, dict):
            meta = obj.get("metadata", {})
            if isinstance(meta, dict) and meta.get("id") and meta.get("url"):
                produto_url = meta.get("url", "")
                if not produto_url.startswith("http"):
                    produto_url = "https://" + produto_url

                preco_atual = None
                preco_original = None

                def _buscar_preco(node, d=0):
                    nonlocal preco_atual, preco_original
                    if d > 8 or not isinstance(node, dict):
                        return
                    if node.get("id") == "price":
                        p = node.get("price", {})
                        if isinstance(p, dict):
                            curr = p.get("value")
                            if curr and not preco_atual:
                                try:
                                    preco_atual = float(curr)
                                except Exception:
                                    pass
                            prev = p.get("previous_price") or p.get("original_price") or {}
                            if isinstance(prev, dict) and prev.get("value") and not preco_original:
                                try:
                                    preco_original = float(prev["value"])
                                except Exception:
                                    pass
                    for v in node.values():
                        if isinstance(v, dict):
                            _buscar_preco(v, d + 1)
                        elif isinstance(v, list):
                            for i in v:
                                if isinstance(i, dict):
                                    _buscar_preco(i, d + 1)

                _buscar_preco(obj)

                titulo = meta.get("title", "") or obj.get("title", "")
                if not titulo:
                    def _buscar_titulo(node, d=0):
                        if d > 6 or not isinstance(node, dict):
                            return ""
                        if node.get("id") == "title" and isinstance(node.get("text"), str):
                            return node["text"]
                        for v in node.values():
                            if isinstance(v, dict):
                                r = _buscar_titulo(v, d + 1)
                                if r:
                                    return r
                            elif isinstance(v, list):
                                for i in v:
                                    if isinstance(i, dict):
                                        r = _buscar_titulo(i, d + 1)
                                        if r:
                                            return r
                        return ""
                    titulo = _buscar_titulo(obj)
                titulo = (titulo or "").replace("�", "").strip()

                foto = None
                pics = obj.get("pictures") or obj.get("picture") or {}
                if isinstance(pics, dict):
                    foto = pics.get("url") or pics.get("src")
                elif isinstance(pics, list) and pics:
                    foto = pics[0].get("url") if isinstance(pics[0], dict) else pics[0]

                if produto_url and (preco_atual or titulo):
                    desc = (
                        round((1 - preco_atual / preco_original) * 100, 1)
                        if preco_atual and preco_original and preco_original > preco_atual
                        else 0.0
                    )
                    produtos.append({
                        "ml_id":          meta.get("id", ""),
                        "titulo":         titulo,
                        "preco":          preco_atual,
                        "preco_original": preco_original if preco_original and preco_atual and preco_original > preco_atual else None,
                        "desconto_pct":   desc,
                        "link":           produto_url,
                        "foto":           foto,
                    })
                return

            for v in obj.values():
                _percorrer(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _percorrer(item, depth + 1)

    _percorrer(dados)
    return produtos


def _filtrar_e_afiliar(produtos: list[dict], nicho: str, desconto_min: int, limite: int) -> list[dict]:
    resultado: list[dict] = []
    vistos: set[str] = set()
    for p in produtos:
        if not p.get("link"):
            continue
        link_limpo = p["link"].split("?")[0]
        if link_limpo in vistos:
            continue
        vistos.add(link_limpo)
        p["link"] = _gerar_link_afiliado(p["link"])
        p["categoria"] = nicho
        p["canal"] = "geral"
        if p.get("desconto_pct", 0) >= desconto_min:
            resultado.append(p)
            if len(resultado) >= limite:
                break
    return resultado


# ── API Async (para rastreador.py dentro do asyncio) ──────────────────────────

async def buscar_ofertas_browser_async(nicho: str, desconto_min: int = 10, limite: int = 20) -> list[dict]:
    """Versão async — use dentro de rastreador.py (asyncio)."""
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    from dotenv import load_dotenv
    load_dotenv()

    url_pagina = _FILTROS_CATEGORIA.get(nicho, "https://www.mercadolivre.com.br/ofertas")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="pt-BR", user_agent=_UA,
            viewport={"width": 1280, "height": 1024},
        )
        page = await ctx.new_page()

        try:
            await page.goto(url_pagina, wait_until="networkidle", timeout=40000)
        except PlaywrightTimeout:
            await page.goto(url_pagina, wait_until="domcontentloaded", timeout=20000)

        try:
            await page.wait_for_selector(
                ".andes-card.poly-card, [class*='poly-card--grid']", timeout=10000
            )
        except PlaywrightTimeout:
            pass

        html = await page.content()
        produtos = _extrair_produtos_json(html)

        if not produtos:
            raw = await page.evaluate(_DOM_SCRIPT)
            produtos = _normalizar_dom(raw)

        await browser.close()

    return _filtrar_e_afiliar(produtos, nicho, desconto_min, limite)


# ── API Sync (para scripts standalone e testes) ───────────────────────────────

def buscar_ofertas_browser(nicho: str, desconto_min: int = 10, limite: int = 20) -> list[dict]:
    """Versão sync — para adicionar_produto.py e scripts standalone."""
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    from dotenv import load_dotenv
    load_dotenv()

    url_pagina = _FILTROS_CATEGORIA.get(nicho, "https://www.mercadolivre.com.br/ofertas")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="pt-BR", user_agent=_UA,
            viewport={"width": 1280, "height": 1024},
        )
        page = ctx.new_page()

        try:
            page.goto(url_pagina, wait_until="networkidle", timeout=40000)
        except PlaywrightTimeout:
            page.goto(url_pagina, wait_until="domcontentloaded", timeout=20000)

        try:
            page.wait_for_selector(
                ".andes-card.poly-card, [class*='poly-card--grid']", timeout=10000
            )
        except PlaywrightTimeout:
            pass

        html = page.content()
        produtos = _extrair_produtos_json(html)

        if not produtos:
            raw = page.evaluate(_DOM_SCRIPT)
            produtos = _normalizar_dom(raw)

        browser.close()

    return _filtrar_e_afiliar(produtos, nicho, desconto_min, limite)
