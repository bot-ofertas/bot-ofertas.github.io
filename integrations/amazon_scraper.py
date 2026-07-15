# -*- coding: utf-8 -*-
"""
Scraper de cupons e ofertas da Amazon Brasil com link de afiliado.

Configuração:
    AMAZON_AFFILIATE_TAG=meublog-20   ← no .env ou GitHub Secrets
    (sem essa variável, o módulo retorna lista vazia sem erro)

Uso standalone:
    python -m integrations.amazon_scraper

Integração com rastreador_amazon.py:
    from integrations.amazon_scraper import buscar_cupons_amazon_async
"""
from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("amazon_scraper")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# Páginas da Amazon Brasil que agregam promoções e cupons
_URLS_AMAZON: list[tuple[str, str]] = [
    ("cupons",      "https://www.amazon.com.br/coupons"),
    ("ofertas_dia", "https://www.amazon.com.br/deals"),
    ("eletronicos", "https://www.amazon.com.br/s?i=electronics&rh=p_n_deal_type%3A23566064011"),
    ("informatica", "https://www.amazon.com.br/s?i=computers&rh=p_n_deal_type%3A23566064011"),
    ("casa",        "https://www.amazon.com.br/s?i=kitchen&rh=p_n_deal_type%3A23566064011"),
]

_DOM_SCRIPT = r"""
() => {
    const resultado = [];

    // Seletores para diferentes layouts da Amazon
    const seletores = [
        '[data-testid="deal-card"]',
        '[data-component-type="s-search-result"]',
        '[data-asin]',
        '.a-carousel-card',
        '.octopus-pc-item',
    ];

    let cards = [];
    for (const sel of seletores) {
        const found = Array.from(document.querySelectorAll(sel));
        if (found.length > 3) { cards = found; break; }
    }

    for (const card of cards.slice(0, 40)) {
        try {
            // Link do produto
            const linkEl = card.querySelector('a[href*="/dp/"], a[href*="/gp/product/"]');
            if (!linkEl) continue;
            let link = linkEl.href || '';
            if (!link.includes('amazon.com.br')) continue;
            // Extrai o ASIN de qualquer posição da URL — a Amazon hoje inclui o
            // slug do nome do produto antes de /dp/ (ex: amazon.com.br/kindle-x/dp/ASIN),
            // então não dá pra exigir /dp/ logo após o domínio.
            const asinMatch = link.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/);
            if (!asinMatch) continue;
            link = `https://www.amazon.com.br/dp/${asinMatch[1]}`;

            // Título
            const tituloEl = card.querySelector(
                'h2 a span, h2 span, .a-size-medium.a-color-base, .a-text-normal, ' +
                '[data-testid="product-title"], .a-size-base-plus'
            );
            const titulo = tituloEl ? tituloEl.textContent.trim() : '';
            if (!titulo || titulo.length < 5) continue;

            // Preço atual
            const precoEl = card.querySelector(
                '.a-price:not(.a-text-price) .a-offscreen, ' +
                '.a-price-whole, [data-testid="price-amount"]'
            );
            const precoTexto = precoEl ? precoEl.textContent.trim() : '';

            // Preço original (riscado)
            const origEl = card.querySelector(
                '.a-price.a-text-price .a-offscreen, .a-text-strike, ' +
                '[data-testid="original-price"]'
            );
            const origTexto = origEl ? origEl.textContent.trim() : '';

            // Badge de desconto/economia
            const descEl = card.querySelector(
                '.savingsPercentage, .a-badge-text, ' +
                '[data-testid="deal-badge"], .octopus-pc-asin-badge'
            );
            const descTexto = descEl ? descEl.textContent.trim() : '';

            // Cupom (badge específico de cupom)
            const cupomEl = card.querySelector(
                '.s-coupon-highlight-color, .couponText, ' +
                '[data-testid="coupon"], .a-color-success'
            );
            const cupomTexto = cupomEl ? cupomEl.textContent.trim() : '';

            // Foto
            const imgEl = card.querySelector('img.s-image, img[data-image-index], img');
            const foto = imgEl ? (imgEl.src || '') : '';

            resultado.push({ titulo, link, precoTexto, origTexto, descTexto, foto, cupomTexto });
        } catch(e) {}
    }
    return resultado;
}
"""


def _link_afiliado(url: str) -> str:
    """Adiciona tag de afiliado à URL da Amazon."""
    if not _AFFILIATE_TAG:
        return url
    base = url.split("?")[0]
    return f"{base}?tag={_AFFILIATE_TAG}&linkCode=as2"


def _preco(texto: str) -> float | None:
    """Extrai preço de 'R$ 1.234,56' ou '1234.56'."""
    if not texto:
        return None
    limpo = re.sub(r"[^\d,]", "", texto.replace(".", ""))
    limpo = limpo.replace(",", ".")
    try:
        v = float(limpo)
        return v if v > 0.5 else None
    except ValueError:
        return None


def _normalizar(raw: list, categoria: str) -> list[dict]:
    vistos: set[str] = set()
    produtos: list[dict] = []

    for item in raw or []:
        titulo = item.get("titulo", "").replace("�", "").strip()
        link = item.get("link", "").strip()
        if not titulo or not link:
            continue
        if link in vistos:
            continue
        vistos.add(link)

        preco = _preco(item.get("precoTexto", ""))
        orig  = _preco(item.get("origTexto", ""))

        m = re.search(r"(\d+)\s*%", item.get("descTexto", ""))
        if m:
            desconto = float(m.group(1))
        elif preco and orig and orig > preco:
            desconto = round((1 - preco / orig) * 100, 1)
        else:
            desconto = 0.0

        # Cupom: normaliza texto do badge
        cupom_raw = item.get("cupomTexto", "").strip()
        cupom = None
        if cupom_raw and len(cupom_raw) > 3:
            # Filtra falsos positivos (ex: textos genéricos "Aproveite")
            if any(k in cupom_raw.lower() for k in ("%", "cupom", "economize", "desconto", "off")):
                cupom = cupom_raw

        foto = item.get("foto", "")
        if foto and not foto.startswith("http"):
            foto = None

        produtos.append({
            "titulo":         titulo,
            "preco":          preco,
            "preco_original": orig if orig and preco and orig > preco else None,
            "desconto_pct":   desconto,
            "link":           _link_afiliado(link),
            "foto":           foto,
            "cupom":          cupom,
            "fonte":          "amazon",
            "categoria":      categoria,
            "canal":          "geral",
        })
    return produtos


async def buscar_cupons_amazon_async(
    desconto_min: int = 10,
    limite: int = 10,
    priorizar_cupom: bool = True,
) -> list[dict]:
    """
    Scrapa cupons e ofertas da Amazon Brasil.

    Args:
        desconto_min: ignora produtos com desconto menor que este valor.
        limite: máximo de produtos retornados.
        priorizar_cupom: coloca produtos com cupom à frente.

    Returns:
        Lista de dicts no mesmo formato de ml_browser.py.
        Retorna [] se AMAZON_AFFILIATE_TAG não estiver configurada.
    """
    if not _AFFILIATE_TAG:
        return []

    from playwright.async_api import async_playwright, TimeoutError as PWT
    from dotenv import load_dotenv
    load_dotenv()

    todos: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="pt-BR", user_agent=_UA,
            viewport={"width": 1280, "height": 1024},
        )

        for categoria, url in _URLS_AMAZON:
            if len(todos) >= limite * 2:
                break
            page = await ctx.new_page()
            try:
                try:
                    await page.goto(url, wait_until="networkidle", timeout=30000)
                except PWT:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)

                # Aguarda lazy-load dos cards
                await page.wait_for_timeout(2500)

                raw = await page.evaluate(_DOM_SCRIPT)
                produtos = _normalizar(raw, categoria)

                # Filtra por desconto mínimo
                if desconto_min > 0:
                    produtos = [p for p in produtos if (p.get("desconto_pct") or 0) >= desconto_min
                                or p.get("cupom")]  # cupom sempre passa

                todos.extend(produtos)
            except Exception as e:
                try:
                    from core.error_logger import log_erro  # noqa: PLC0415
                    log_erro("amazon_scraper.categoria", e, {"categoria": categoria, "url": url})
                except Exception:
                    log.warning("amazon scraper falhou em %s (%s): %s", categoria, url, e)
            finally:
                try:
                    await page.close()
                except Exception:
                    pass  # página pode já ter crashado — nada a fechar

        try:
            await browser.close()
        except Exception:
            pass  # browser pode ter morrido junto com uma página crashada;
                   # não pode derrubar os produtos já coletados nas categorias anteriores

    if priorizar_cupom:
        com_cupom = [p for p in todos if p.get("cupom")]
        sem_cupom = [p for p in todos if not p.get("cupom")]
        todos = com_cupom + sem_cupom

    return todos[:limite]


def amazon_ativo() -> bool:
    """True se AMAZON_AFFILIATE_TAG está configurada."""
    return bool(_AFFILIATE_TAG)


# ── CLI para teste ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio, sys
    sys.stdout.reconfigure(encoding="utf-8")
    from dotenv import load_dotenv
    load_dotenv()

    if not amazon_ativo():
        print("AMAZON_AFFILIATE_TAG não configurada no .env")
        print("Adicione: AMAZON_AFFILIATE_TAG=seu-tag-20")
        sys.exit(1)

    print(f"Tag de afiliado: {_AFFILIATE_TAG}")
    print("Buscando cupons Amazon Brasil...\n")

    produtos = asyncio.run(buscar_cupons_amazon_async(desconto_min=10, limite=5))
    for p in produtos:
        print(f"{'[CUPOM]' if p.get('cupom') else '      '} {p['titulo'][:60]}")
        if p.get("preco"):
            print(f"         R$ {p['preco']:.2f}", end="")
            if p.get("desconto_pct"):
                print(f" ({p['desconto_pct']:.0f}% OFF)", end="")
            print()
        if p.get("cupom"):
            print(f"         Cupom: {p['cupom']}")
        print(f"         {p['link'][:80]}\n")

    print(f"Total: {len(produtos)} produto(s)")
