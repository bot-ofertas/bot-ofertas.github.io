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

# Páginas da Amazon Brasil que agregam promoções e cupons.
# Ampliado em 2026-08-01: só 3 departamentos (eletronicos/informatica/casa)
# + 2 agregadores esgotavam rápido (quase 100% duplicata em rodadas
# consecutivas — catálogo de ofertas da Amazon nesses departamentos é
# pequeno e roda devagar). Os 10 departamentos abaixo foram validados ao
# vivo com o mesmo filtro rh=p_n_deal_type já usado, retornando produtos
# nunca vistos pelo bot.
# As duas primeiras sao AGREGADORES CURADOS pela propria Amazon (a pagina de
# cupons e a de ofertas do dia) — sao a razao de este rastreador existir e a
# unica fonte onde o badge de cupom aparece. Ficam FORA do sorteio, sempre
# visitadas primeiro.
#
# Bug real, achado em 2026-09-17 nos logs das rodadas #277, #280 e #283 (todas
# com "0 com cupom de desconto"): as 15 URLs eram embaralhadas juntas e o laco
# para assim que junta `limite*2` produtos — o que acontece depois de 2 ou 3
# categorias. A pagina de cupons entrava numa loteria de 15 e perdia na
# maioria das rodadas, entao um "Rastreador Amazon Cupons" passava rodadas
# inteiras sem olhar cupom nenhum.
_FONTES_CURADAS: list[tuple[str, str]] = [
    ("cupons",           "https://www.amazon.com.br/coupons"),
    ("ofertas_dia",      "https://www.amazon.com.br/deals"),
]

# Departamentos: continuam embaralhados a cada rodada. Aqui o sorteio E
# desejado — sempre varrer na mesma ordem faria as categorias do fim da lista
# quase nunca serem alcancadas (motivo da ampliacao de 2026-08-01).
_URLS_AMAZON: list[tuple[str, str]] = [
    ("eletronicos",      "https://www.amazon.com.br/s?i=electronics&rh=p_n_deal_type%3A23566064011"),
    ("informatica",      "https://www.amazon.com.br/s?i=computers&rh=p_n_deal_type%3A23566064011"),
    ("casa",             "https://www.amazon.com.br/s?i=kitchen&rh=p_n_deal_type%3A23566064011"),
    ("moda",             "https://www.amazon.com.br/s?i=fashion&rh=p_n_deal_type%3A23566064011"),
    ("brinquedos",       "https://www.amazon.com.br/s?i=toys&rh=p_n_deal_type%3A23566064011"),
    ("esportes",         "https://www.amazon.com.br/s?i=sporting&rh=p_n_deal_type%3A23566064011"),
    ("beleza",           "https://www.amazon.com.br/s?i=beauty&rh=p_n_deal_type%3A23566064011"),
    ("ferramentas",      "https://www.amazon.com.br/s?i=hi&rh=p_n_deal_type%3A23566064011"),
    ("games",            "https://www.amazon.com.br/s?i=videogames&rh=p_n_deal_type%3A23566064011"),
    ("automotivo",       "https://www.amazon.com.br/s?i=automotive&rh=p_n_deal_type%3A23566064011"),
    ("pet",              "https://www.amazon.com.br/s?i=pets&rh=p_n_deal_type%3A23566064011"),
    ("bebes",            "https://www.amazon.com.br/s?i=baby&rh=p_n_deal_type%3A23566064011"),
    ("eletrodomesticos", "https://www.amazon.com.br/s?i=appliances&rh=p_n_deal_type%3A23566064011"),
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


# Roda SO quando a categoria devolve zero produtos. Nao mexe na raspagem: so
# responde "zero por que?". Sem isso o log dizia "seletor do DOM provavelmente
# mudou" — uma SUPOSICAO (Regra 2), e a errada na maior parte das vezes: uma
# pagina de bloqueio anti-bot da Amazon tambem devolve zero card, e o
# tratamento e o oposto (esperar/trocar de saida, nao reescrever seletor).
_DIAG_SCRIPT = r"""
() => {
    const seletores = [
        '[data-testid="deal-card"]',
        '[data-component-type="s-search-result"]',
        '[data-asin]',
        '.a-carousel-card',
        '.octopus-pc-item',
    ];
    const contagem = {};
    for (const sel of seletores) {
        contagem[sel] = document.querySelectorAll(sel).length;
    }
    const texto = (document.body ? document.body.innerText : '') || '';
    const marcas_bloqueio = [
        'Digite os caracteres',
        'Type the characters',
        'Continuar comprando',
        'automated access',
        'Para discutir o acesso automatizado',
        'Sorry, we just need to make sure',
        'Desculpe-nos',
    ];
    const achadas = marcas_bloqueio.filter(m => texto.includes(m));

    // Pagina de erro/throttle da Amazon: titulo "Algo deu errado" (ou o
    // equivalente em ingles) com o body VAZIO. Nao e captcha — nao tem
    // formulario nem instrucao — e nao e seletor: e a Amazon recusando
    // servir a pagina naquele instante.
    const titulo_ = document.title || '';
    const erro_servidor = (
        titulo_.includes('Algo deu errado') ||
        titulo_.includes('Something went wrong') ||
        titulo_.includes('Desculpe') ||
        (texto.length === 0 && titulo_.length > 0)
    );
    return {
        erro_servidor: erro_servidor,
        titulo:    (document.title || '').slice(0, 120),
        url_final: location.href.slice(0, 200),
        contagem:  contagem,
        tamanho_texto: texto.length,
        bloqueio:  achadas,
        tem_captcha: !!document.querySelector(
            '#captchacharacters, form[action*="validateCaptcha"]'),
    };
}
"""


def _link_afiliado(url: str) -> str:
    """Adiciona tag de afiliado à URL da Amazon.

    ascsubtag identifica a origem (telegram por padrão aqui) sem precisar de
    uma Associate Tag separada por canal — ver marcar_link_para_whatsapp()
    em integrations/whatsapp_sender.py, que troca esse valor pra "bot_whatsapp"
    só na mensagem do WhatsApp, pra cada plataforma ter seu próprio link em
    vez de publicar literalmente a mesma URL nos dois lugares.
    """
    if not _AFFILIATE_TAG:
        return url
    base = url.split("?")[0].split("#")[0]
    return f"{base}?tag={_AFFILIATE_TAG}&linkCode=as2&ascsubtag=bot_telegram"


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


# Pausa antes da segunda tentativa quando a Amazon devolve a pagina de erro.
# Curta de proposito: e para atravessar um throttle de instante, nao para
# insistir numa loja que esta recusando — insistir e o caminho de virar
# bloqueio de verdade.
_PAUSA_RETENTATIVA_MS = 3000


async def _extrair_da_pagina(page, categoria: str, desconto_min: int):
    """(cards crus, produtos filtrados) de uma pagina ja carregada.

    Existe para que a primeira tentativa e a retentativa nao tenham duas
    copias do mesmo filtro — duas copias e onde uma delas fica para tras.
    """
    raw = await page.evaluate(_DOM_SCRIPT)
    produtos = _normalizar(raw, categoria)
    if desconto_min > 0:
        # cupom sempre passa, mesmo abaixo do desconto minimo
        produtos = [p for p in produtos
                    if (p.get("desconto_pct") or 0) >= desconto_min or p.get("cupom")]
    return raw, produtos


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

    import random  # noqa: PLC0415
    from playwright.async_api import async_playwright, TimeoutError as PWT
    from dotenv import load_dotenv
    load_dotenv()

    todos: list[dict] = []

    # Ordem embaralhada a cada chamada (sem mutar a lista do módulo — evita
    # problema de concorrência entre chamadas simultâneas). Com 15 URLs e o
    # corte antecipado de limite*2, sempre escanear na mesma ordem faria as
    # categorias do fim da lista quase nunca serem alcançadas.
    # Curadas primeiro, sempre; departamentos sorteados depois.
    ordem = _FONTES_CURADAS + random.sample(_URLS_AMAZON, len(_URLS_AMAZON))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            # new_context() também precisa estar DENTRO do try — se falhar
            # (timeout, subprocesso do chromium crashado, exaustão de
            # recursos), o finally abaixo ainda fecha o browser já lançado,
            # em vez de vazar um chromium.exe órfão.
            ctx = await browser.new_context(
                locale="pt-BR", user_agent=_UA,
                viewport={"width": 1280, "height": 1024},
            )

            for categoria, url in ordem:
                if len(todos) >= limite * 2:
                    break
                page = None
                try:
                    # new_page() também pode falhar se o browser morreu numa
                    # categoria anterior (página crashada) — precisa estar DENTRO
                    # do try, senão a exceção escapa do for inteiro e descarta
                    # os produtos já coletados nas categorias anteriores
                    page = await ctx.new_page()
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=30000)
                    except PWT:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)

                    # Aguarda lazy-load dos cards
                    await page.wait_for_timeout(2500)

                    raw, produtos = await _extrair_da_pagina(
                        page, categoria, desconto_min)

                    # Quantos vieram e quantos traziam badge de cupom, POR
                    # fonte. Sem isso, "0 com cupom" no resumo final e mudo:
                    # nao distingue "a pagina de cupons nao foi visitada" de
                    # "foi visitada e o seletor nao casa mais" — e foi
                    # justamente essa mudez que escondeu o bug da ordem
                    # aleatoria por rodadas seguidas.
                    _com_cupom = sum(1 for x in produtos if x.get("cupom"))
                    log.info("amazon[%s]: %d card(s) no DOM, %d produto(s) apos "
                             "filtro, %d com cupom",
                             categoria, len(raw), len(produtos), _com_cupom)

                    # Zero produto tem tres causas diferentes e so o DOM
                    # distingue: (a) a pagina veio e os cards nao casam mais
                    # com o seletor, (b) a pagina veio, os cards casaram e o
                    # filtro de desconto cortou tudo, (c) a Amazon devolveu
                    # pagina de bloqueio/captcha e nao ha pagina nenhuma.
                    # Tratar (c) como (a) leva a reescrever seletor que esta
                    # certo. So pergunta quando da zero — nenhuma chamada
                    # extra na rodada saudavel.
                    if not produtos:
                        try:
                            d = await page.evaluate(_DIAG_SCRIPT)
                        except Exception:
                            d = None
                        if d:
                            if d.get("bloqueio") or d.get("tem_captcha"):
                                log.warning(
                                    "amazon[%s]: BLOQUEIO anti-bot — a pagina nao "
                                    "chegou a carregar produtos (marcas=%s captcha=%s "
                                    "titulo=%r). Nao e seletor: nao mexer no DOM.",
                                    categoria, d.get("bloqueio"), d.get("tem_captcha"),
                                    d.get("titulo"))
                            elif d.get("erro_servidor"):
                                log.warning(
                                    "amazon[%s]: a Amazon nao serviu a pagina "
                                    "(titulo=%r, corpo vazio) — throttle/erro do "
                                    "lado deles. Nao e seletor: outras categorias "
                                    "da mesma rodada trazem cards normalmente.",
                                    categoria, d.get("titulo"))
                                # UMA segunda tentativa, so neste caso. Na
                                # rodada #291 treze das quinze fontes cairam
                                # aqui e uma unica (brinquedos) passou e trouxe
                                # 24 cards — ou seja, a recusa e por requisicao,
                                # nao pela rodada inteira. Uma so: repetir ate
                                # conseguir e o que transforma throttle em
                                # bloqueio.
                                try:
                                    await page.wait_for_timeout(_PAUSA_RETENTATIVA_MS)
                                    await page.reload(wait_until="domcontentloaded",
                                                      timeout=20000)
                                    await page.wait_for_timeout(2500)
                                    raw, produtos = await _extrair_da_pagina(
                                        page, categoria, desconto_min)
                                    if produtos:
                                        log.info(
                                            "amazon[%s]: recuperado na 2a tentativa "
                                            "— %d card(s), %d produto(s)",
                                            categoria, len(raw), len(produtos))
                                except Exception as e2:
                                    log.info("amazon[%s]: 2a tentativa tambem falhou: %s",
                                             categoria, e2)
                            elif len(raw) > 0:
                                log.warning(
                                    "amazon[%s]: %d card(s) extraidos e nenhum passou "
                                    "o filtro de desconto >= %d%% — pagina saudavel, "
                                    "oferta fraca.", categoria, len(raw), desconto_min)
                            else:
                                log.warning(
                                    "amazon[%s]: ZERO card no DOM sem marca de bloqueio "
                                    "— seletores=%s texto=%d titulo=%r url=%s",
                                    categoria, d.get("contagem"),
                                    d.get("tamanho_texto"), d.get("titulo"),
                                    d.get("url_final"))

                    todos.extend(produtos)
                except Exception as e:
                    try:
                        from core.error_logger import log_erro  # noqa: PLC0415
                        log_erro("amazon_scraper.categoria", e, {"categoria": categoria, "url": url})
                    except Exception:
                        log.warning("amazon scraper falhou em %s (%s): %s", categoria, url, e)
                finally:
                    if page is not None:
                        try:
                            await page.close()
                        except Exception:
                            pass  # página pode já ter crashado — nada a fechar
        finally:
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
