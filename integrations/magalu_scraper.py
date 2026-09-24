# -*- coding: utf-8 -*-
"""Scraper de ofertas do Magazine Luiza.

DECISAO DE PROJETO — por que este scraper NAO comeca por seletor CSS.

O site do Magalu e um Next.js: a pagina carrega com um `<script
id="__NEXT_DATA__">` que ja traz, em JSON, o mesmo catalogo que o HTML
depois desenha. Ler esse JSON e mais estavel do que ler o DOM por duas
razoes concretas, as duas ja pagas caro neste projeto:

1. Classe CSS gerada por build muda sem aviso. Foi exatamente isso que
   quebrou a extracao de titulo da Amazon (`ProductCard-module__title_<hash>`)
   e custou uma rodada inteira de diagnostico.
2. Card renderizado por JS depende de lazy-load: o seletor pode estar
   certo e a lista vir vazia so porque a rolagem nao aconteceu.

O DOM fica como SEGUNDA tentativa, nao como primeira. E quando as duas
falham, `_diagnosticar()` diz POR QUE deu zero — bloqueio, pagina de erro,
layout novo ou filtro cortando tudo — em vez de devolver `[]` mudo, que e
o que transforma "nao achou oferta" num mistério de log (Regra 2/9).

ESTADO: NAO VALIDADO contra o site ao vivo. O ambiente onde este arquivo
foi escrito bloqueia `www.magazineluiza.com.br` por politica de proxy
(403 no CONNECT), entao os caminhos de extracao foram testados apenas
contra HTML sintetico (`tests/test_magalu.py`). A primeira rodada real no
PC e que vale como validacao — e e por isso que o diagnostico acima existe
e e verboso.
"""
from __future__ import annotations

import json
import os
import re

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# Paginas de oferta do Magalu. Curadas primeiro (desconto de verdade),
# departamentos depois — mesma ordem de prioridade do scraper da Amazon.
_FONTES_CURADAS: list[tuple[str, str]] = [
    ("ofertas-do-dia", "https://www.magazineluiza.com.br/selecao/ofertasdodia/"),
    ("mais-vendidos",  "https://www.magazineluiza.com.br/mais-vendidos/"),
]

_URLS_MAGALU: list[tuple[str, str]] = [
    ("celulares",       "https://www.magazineluiza.com.br/celulares-e-smartphones/l/te/"),
    ("informatica",     "https://www.magazineluiza.com.br/informatica/l/in/"),
    ("tvs",             "https://www.magazineluiza.com.br/tv-e-video/l/et/"),
    ("eletrodomesticos", "https://www.magazineluiza.com.br/eletrodomesticos/l/ed/"),
    ("eletroportateis", "https://www.magazineluiza.com.br/eletroportateis/l/ep/"),
    ("games",           "https://www.magazineluiza.com.br/games/l/ga/"),
    ("moveis",          "https://www.magazineluiza.com.br/moveis/l/mo/"),
    ("beleza",          "https://www.magazineluiza.com.br/perfumaria/l/pf/"),
]

_TIMEOUT_MS = 30000
_PAUSA_LAZY_MS = 2500


# ── preço ────────────────────────────────────────────────────────────────
def _preco(valor) -> float | None:
    """Converte preço em float. Aceita número ou texto no formato BR.

    O JSON do Magalu as vezes traz `1899.9` (float) e as vezes
    `"R$ 1.899,90"` (string, quando vem do DOM). Os dois formatos caem
    aqui — separar em duas funcoes foi o que, na Amazon, deixou um dos
    caminhos sem conversao e produziu `preco=None` em oferta valida.
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor) if valor > 0 else None
    texto = str(valor)
    achado = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+[.,]\d{2}|\d+)", texto)
    if not achado:
        return None
    bruto = achado.group(1)
    if "," in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    try:
        numero = float(bruto)
    except ValueError:
        return None
    return numero if numero > 0 else None


# ── extração do __NEXT_DATA__ ────────────────────────────────────────────
# Um produto do Magalu, no JSON, e um dict que tem titulo E (preco OU
# caminho de produto). Procurar por FORMA em vez de por caminho fixo
# ("props.pageProps.data.search.products") e o que faz isto sobreviver a
# uma reorganizacao do estado do Next — que acontece a cada refatoracao
# deles e nao tem nada a ver com o catalogo.
_CHAVES_TITULO = ("title", "titulo", "name", "productTitle")
_CHAVES_PRECO = ("price", "bestPrice", "cashPrice", "salePrice", "preco", "by")
_CHAVES_PRECO_CHEIO = ("listPrice", "fullPrice", "originalPrice", "from", "precoOriginal")
_CHAVES_LINK = ("url", "path", "link", "canonicalUrl", "seoUrl")
_CHAVES_FOTO = ("image", "imageUrl", "thumbnail", "picture", "images")


def _texto_de(no: dict, chaves) -> str | None:
    for chave in chaves:
        valor = no.get(chave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return None


def _numero_de(no: dict, chaves) -> float | None:
    """Preço pode vir direto (`price: 99.9`) ou aninhado (`price: {by: 99.9}`)."""
    for chave in chaves:
        if chave not in no:
            continue
        valor = no[chave]
        if isinstance(valor, dict):
            aninhado = _numero_de(valor, _CHAVES_PRECO + _CHAVES_PRECO_CHEIO + ("value", "amount"))
            if aninhado:
                return aninhado
            continue
        convertido = _preco(valor)
        if convertido:
            return convertido
    return None


def _foto_de(no: dict) -> str | None:
    for chave in _CHAVES_FOTO:
        valor = no.get(chave)
        if isinstance(valor, str) and valor.startswith("http"):
            return valor
        if isinstance(valor, dict):
            interno = _texto_de(valor, ("url", "src", "large", "medium", "default"))
            if interno and interno.startswith("http"):
                return interno
        if isinstance(valor, list):
            for elemento in valor:
                if isinstance(elemento, str) and elemento.startswith("http"):
                    return elemento
                if isinstance(elemento, dict):
                    interno = _texto_de(elemento, ("url", "src"))
                    if interno and interno.startswith("http"):
                        return interno
    return None


def _absolutizar(caminho: str) -> str | None:
    """Caminho relativo do JSON vira URL completa do magazineluiza."""
    if not caminho:
        return None
    if caminho.startswith("http"):
        return caminho
    if not caminho.startswith("/"):
        caminho = "/" + caminho
    return "https://www.magazineluiza.com.br" + caminho


# Um link de produto do Magalu sempre tem `/p/<codigo>/`. E o que separa
# produto de banner, de categoria e de link de rodape no mesmo JSON.
_E_PRODUTO = re.compile(r"/p/[0-9]{6,15}(?:/|$)")


def _colher(no, achados: list, profundidade: int = 0) -> None:
    """Percorre o JSON inteiro juntando o que TEM FORMA de produto."""
    if profundidade > 12 or len(achados) > 400:
        return
    if isinstance(no, list):
        for item in no:
            _colher(item, achados, profundidade + 1)
        return
    if not isinstance(no, dict):
        return

    titulo = _texto_de(no, _CHAVES_TITULO)
    link = _absolutizar(_texto_de(no, _CHAVES_LINK) or "")
    if titulo and link and _E_PRODUTO.search(link):
        # Os dois precos podem estar no proprio no (`bestPrice`/`listPrice`
        # soltos) OU dentro de um sub-dict `price`. Procurar so no no de
        # cima achava o preco de venda e perdia o preco cheio, e sem preco
        # cheio o desconto sai 0% e o filtro descarta uma oferta boa —
        # foi exatamente o que o teste pegou (geladeira 30% OFF sumindo).
        escopos = [no] + [
            valor for chave, valor in no.items()
            if chave in _CHAVES_PRECO and isinstance(valor, dict)
        ]
        preco = orig = None
        for escopo in escopos:
            preco = preco or _numero_de(escopo, _CHAVES_PRECO)
            orig = orig or _numero_de(escopo, _CHAVES_PRECO_CHEIO)
        achados.append({
            "titulo": titulo,
            "link": link,
            "preco": preco,
            "preco_original": orig,
            "foto": _foto_de(no),
        })
        # Nao retorna: um card pode carregar variacoes aninhadas.
    for valor in no.values():
        _colher(valor, achados, profundidade + 1)


def extrair_do_next_data(html: str) -> list[dict]:
    """Produtos a partir do `__NEXT_DATA__` do HTML. [] se não houver."""
    achado = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, re.S | re.I,
    )
    if not achado:
        return []
    try:
        dados = json.loads(achado.group(1))
    except Exception:
        return []
    colhidos: list[dict] = []
    _colher(dados, colhidos)
    return colhidos


# ── extração do DOM (segunda tentativa) ──────────────────────────────────
# `data-testid` e atributo de teste do proprio time do Magalu: muda MUITO
# menos que classe de build, porque quebra a suite deles junto. Ainda
# assim e a segunda opcao, nao a primeira.
_JS_DOM = """
() => {
  const saida = [];
  const cards = document.querySelectorAll(
    '[data-testid="product-card-container"], a[href*="/p/"]'
  );
  for (const card of cards) {
    const a = card.matches('a[href]') ? card : card.querySelector('a[href]');
    if (!a || !/\\/p\\/\\d{6,}/.test(a.getAttribute('href') || '')) continue;
    const t = (card.querySelector('[data-testid="product-title"]')
            || card.querySelector('h2, h3')
            || card.querySelector('img[alt]'));
    const titulo = t ? (t.textContent || t.getAttribute('alt') || '').trim() : '';
    const precoEl = card.querySelector('[data-testid="price-value"], [data-testid="price"]');
    const origEl  = card.querySelector('[data-testid="price-original"], s, del');
    const img     = card.querySelector('img');
    if (!titulo) continue;
    saida.push({
      titulo,
      link: a.href,
      precoTexto: precoEl ? precoEl.textContent : '',
      origTexto:  origEl  ? origEl.textContent  : '',
      foto: img ? (img.getAttribute('src') || img.getAttribute('data-src') || '') : ''
    });
  }
  return saida;
}
"""


def _do_dom(brutos: list) -> list[dict]:
    saida = []
    for item in brutos or []:
        saida.append({
            "titulo": (item.get("titulo") or "").strip(),
            "link": item.get("link") or "",
            "preco": _preco(item.get("precoTexto")),
            "preco_original": _preco(item.get("origTexto")),
            "foto": (item.get("foto") or "") or None,
        })
    return saida


# ── normalização ─────────────────────────────────────────────────────────
def normalizar(brutos: list[dict], categoria: str, desconto_min: int) -> list[dict]:
    """Mesma forma de dict que ml_browser e amazon_scraper devolvem.

    O link sai LIMPO (sem query e sem fragmento). Quem poe a marcacao de
    afiliado e `affiliates/magalu.py`, um passo depois — e a Regra 11
    manda o fragmento morrer antes de qualquer coisa, senao ele leva o
    parametro de origem junto e o ID de deduplicacao muda a cada raspagem.
    """
    vistos: set[str] = set()
    produtos: list[dict] = []

    for item in brutos or []:
        titulo = (item.get("titulo") or "").strip()
        link = (item.get("link") or "").split("?")[0].split("#")[0]
        if not titulo or not link or not _E_PRODUTO.search(link):
            continue

        # Deduplicacao pelo CODIGO oficial do anuncio, nao pelo slug: o
        # mesmo produto aparece com slugs diferentes em vitrines
        # diferentes (Regra 11).
        codigo = _E_PRODUTO.search(link).group(0).strip("/").split("/")[-1]
        if codigo in vistos:
            continue
        vistos.add(codigo)

        preco = item.get("preco")
        orig = item.get("preco_original")
        if preco and orig and orig > preco:
            desconto = round((1 - preco / orig) * 100, 1)
        else:
            desconto = 0.0
        if desconto < desconto_min:
            continue

        foto = item.get("foto")
        if foto and not str(foto).startswith("http"):
            foto = None

        produtos.append({
            "titulo": titulo,
            "preco": preco,
            "preco_original": orig if orig and preco and orig > preco else None,
            "desconto_pct": desconto,
            "link": link,
            "foto": foto,
            "cupom": None,
            "fonte": "magalu",
            "categoria": categoria,
            "canal": "geral",
            "codigo": codigo,
        })
    return produtos


# ── diagnóstico do zero ──────────────────────────────────────────────────
_JS_DIAG = """
() => ({
  titulo: document.title || '',
  temNextData: !!document.getElementById('__NEXT_DATA__'),
  tamanhoNextData: (document.getElementById('__NEXT_DATA__') || {textContent:''}).textContent.length,
  linksProduto: document.querySelectorAll('a[href*="/p/"]').length,
  cards: document.querySelectorAll('[data-testid="product-card-container"]').length,
  captcha: /captcha|verifique que voc|acesso negado|forbidden/i.test(document.body.innerText.slice(0, 4000)),
  textoInicio: document.body.innerText.slice(0, 200)
})
"""


def interpretar_diagnostico(diag: dict, brutos_achados: int, pos_filtro: int) -> str:
    """Traduz o diagnóstico em uma causa nomeada.

    Existe porque `[]` nao e diagnostico. A diferenca entre "o Magalu
    bloqueou", "o layout mudou" e "o filtro de desconto cortou tudo" muda
    completamente qual e a correcao — e sem esta funcao as tres chegam ao
    log como o mesmo silencio (Regra 2).
    """
    if not diag:
        return "pagina nao respondeu ao diagnostico (navegador caiu ou timeout)"
    if diag.get("captcha"):
        return "BLOQUEIO anti-bot: a pagina devolveu captcha/acesso negado"
    if brutos_achados == 0:
        if not diag.get("temNextData") and diag.get("linksProduto", 0) == 0:
            return (f"pagina sem catalogo — titulo={diag.get('titulo','')[:60]!r}; "
                    f"provavel erro/redirect, nao layout novo")
        if diag.get("linksProduto", 0) > 0 or diag.get("cards", 0) > 0:
            return (f"LAYOUT MUDOU: a pagina tem {diag.get('linksProduto',0)} link(s) de "
                    f"produto e {diag.get('cards',0)} card(s), mas nem o __NEXT_DATA__ "
                    f"(tamanho={diag.get('tamanhoNextData',0)}) nem o DOM renderam produto "
                    f"reconhecivel — seletores/chaves precisam de revisao")
        return "nenhum produto na pagina (lazy-load nao completou ou selecao vazia)"
    if pos_filtro == 0:
        return (f"FILTRO cortou tudo: {brutos_achados} produto(s) lidos, nenhum com "
                f"desconto suficiente — e configuracao, nao defeito")
    return "ok"


# ── entrada pública ──────────────────────────────────────────────────────
def magalu_ativo() -> bool:
    """Só vale raspar se houver vitrine — sem ela nada pode ser publicado."""
    try:
        from affiliates.magalu import magalu_ativo as _ativo  # noqa: PLC0415
        return _ativo()
    except Exception:
        return False


async def buscar_ofertas_magalu_async(
    desconto_min: int = 20,
    limite: int = 20,
) -> list[dict]:
    """Raspa ofertas do Magazine Luiza.

    Devolve [] (sem levantar) quando `MAGALU_VITRINE` nao esta configurada:
    raspar sem poder gerar link de afiliado so gastaria rede para a Regra 7
    descartar tudo depois.
    """
    if not magalu_ativo():
        return []

    import random  # noqa: PLC0415
    from playwright.async_api import async_playwright, TimeoutError as PWT  # noqa: PLC0415

    from core.error_logger import log_erro  # noqa: PLC0415

    todos: list[dict] = []
    ordem = _FONTES_CURADAS + random.sample(_URLS_MAGALU, len(_URLS_MAGALU))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(
                locale="pt-BR", user_agent=_UA,
                viewport={"width": 1280, "height": 1024},
            )
            for categoria, url in ordem:
                if len(todos) >= limite * 2:
                    break
                page = None
                try:
                    page = await ctx.new_page()
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=_TIMEOUT_MS)
                    except PWT:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(_PAUSA_LAZY_MS)

                    # 1ª tentativa: JSON do Next (estavel).
                    html = await page.content()
                    brutos = extrair_do_next_data(html)
                    origem = "__NEXT_DATA__"

                    # 2ª tentativa: DOM.
                    if not brutos:
                        brutos = _do_dom(await page.evaluate(_JS_DOM))
                        origem = "DOM"

                    produtos = normalizar(brutos, categoria, desconto_min)

                    if not produtos:
                        diag = {}
                        try:
                            diag = await page.evaluate(_JS_DIAG)
                        except Exception:
                            pass
                        causa = interpretar_diagnostico(diag, len(brutos), len(produtos))
                        print(f"  [magalu/{categoria}] 0 oferta(s) — {causa}")
                        log_erro("magalu.zero_ofertas", RuntimeError(causa),
                                 {"categoria": categoria, "url": url,
                                  "brutos": len(brutos), "diag": diag})
                    else:
                        print(f"  [magalu/{categoria}] {len(produtos)} oferta(s) via {origem}")
                        todos.extend(produtos)
                except Exception as erro:
                    log_erro("magalu.categoria_falhou", erro,
                             {"categoria": categoria, "url": url})
                    print(f"  [magalu/{categoria}] falhou: {erro}")
                finally:
                    if page is not None:
                        try:
                            await page.close()
                        except Exception:
                            pass
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    # Maior desconto primeiro, e corta no limite pedido.
    todos.sort(key=lambda p: p.get("desconto_pct") or 0, reverse=True)
    return todos[:limite]
