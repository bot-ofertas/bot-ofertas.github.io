# -*- coding: utf-8 -*-
"""
BLOG GENERATOR — cada oferta vira uma landing page HTML no /web

Objetivo: SEO orgânico. Google indexa as páginas → tráfego passivo perpétuo.
Cada URL é permanente (nunca muda), gerando backlinks/histórico de indexação.

Como funciona:
  1. Rastreador chama gerar_landing(produto) após publicar em canais
  2. Cria web/ofertas/{slug}.html com meta tags SEO, schema.org Product
  3. Atualiza web/index.html com últimas ofertas
  4. Atualiza web/sitemap.xml
  5. GitHub Actions publica no GitHub Pages
"""
from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime

log = logging.getLogger("blog_generator")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(_BASE, "web")
OFERTAS_DIR = os.path.join(WEB_DIR, "ofertas")
os.makedirs(OFERTAS_DIR, exist_ok=True)

SITE_URL = os.getenv("SITE_URL", "https://bot-ofertas.github.io")


def slugify(texto: str, max_len: int = 80) -> str:
    """Converte título em slug URL-safe."""
    if not texto:
        return "oferta"
    s = texto.lower()
    s = re.sub(r"[àáâãä]", "a", s)
    s = re.sub(r"[èéêë]", "e", s)
    s = re.sub(r"[ìíîï]", "i", s)
    s = re.sub(r"[òóôõö]", "o", s)
    s = re.sub(r"[ùúûü]", "u", s)
    s = re.sub(r"[ç]", "c", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:max_len] or "oferta"


def gerar_landing(produto: dict) -> str | None:
    """Gera página HTML SEO-friendly para a oferta.

    Retorna o caminho relativo (ofertas/xxx.html) ou None se falhar.
    """
    try:
        titulo = produto.get("titulo") or ""
        if len(titulo) < 5:
            return None
        pid = produto.get("id") or slugify(titulo)[:12]
        slug = f"{slugify(titulo, 60)}-{pid[:12]}".strip("-")
        arquivo = os.path.join(OFERTAS_DIR, f"{slug}.html")

        preco = produto.get("preco") or 0
        preco_orig = produto.get("preco_original")
        desconto = produto.get("desconto_pct") or 0
        if not desconto and preco_orig and preco:
            desconto = round((1 - preco / preco_orig) * 100)
        foto = produto.get("foto") or ""
        link = produto.get("link") or produto.get("affiliate_link") or "#"
        categoria = produto.get("categoria") or "ofertas"
        fonte = produto.get("fonte") or "ml"
        cupom = produto.get("cupom") or ""

        loja = "Amazon" if fonte == "amazon" else "Mercado Livre"
        agora = datetime.now().strftime("%d/%m/%Y às %H:%M")
        descricao_meta = (
            f"Oferta {loja}: {titulo[:100]} por apenas R$ {preco:.2f}"
            + (f" ({desconto}% OFF)" if desconto else "")
        )

        # Schema.org Product para rich snippets do Google
        schema = {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": titulo,
            "image": foto,
            "offers": {
                "@type": "Offer",
                "url": link,
                "priceCurrency": "BRL",
                "price": preco,
                "availability": "https://schema.org/InStock",
                "seller": {"@type": "Organization", "name": loja},
            },
        }
        if produto.get("hist_preco", {}).get("menor"):
            schema["offers"]["priceValidUntil"] = datetime.now().strftime("%Y-%m-%d")

        import json  # noqa: PLC0415
        schema_json = json.dumps(schema, ensure_ascii=False)

        h = html.escape
        html_page = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{h(titulo[:60])} — R$ {preco:.2f}{f" ({desconto}% OFF)" if desconto else ""} | Bot Ofertas</title>
<meta name="description" content="{h(descricao_meta)}">
<meta name="keywords" content="{h(categoria)}, oferta, {loja.lower()}, promocao, desconto, {h(slugify(titulo).replace('-',' '))}">
<link rel="canonical" href="{SITE_URL}/ofertas/{slug}.html">

<meta property="og:type" content="product">
<meta property="og:title" content="{h(titulo[:60])} — R$ {preco:.2f}">
<meta property="og:description" content="{h(descricao_meta)}">
<meta property="og:image" content="{h(foto)}">
<meta property="og:url" content="{SITE_URL}/ofertas/{slug}.html">
<meta property="product:price:amount" content="{preco}">
<meta property="product:price:currency" content="BRL">

<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{h(titulo[:60])}">
<meta name="twitter:description" content="{h(descricao_meta)}">
<meta name="twitter:image" content="{h(foto)}">

<script type="application/ld+json">{schema_json}</script>

<style>
:root {{ color-scheme: light dark; }}
body {{ font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; margin:0; padding:24px; max-width:800px; margin:0 auto; line-height:1.6; }}
.hero {{ text-align:center; padding:24px 0; }}
.hero img {{ max-width:400px; width:100%; height:auto; border-radius:12px; }}
h1 {{ font-size:26px; margin:16px 0 8px; }}
.preco {{ font-size:32px; font-weight:700; color:#c62828; margin:16px 0 8px; }}
.preco-orig {{ text-decoration:line-through; color:#888; font-size:16px; margin-left:8px; }}
.desconto {{ display:inline-block; background:#c62828; color:#fff; padding:4px 12px; border-radius:6px; font-size:14px; font-weight:600; margin-left:8px; }}
.cta {{ display:block; background:#f97316; color:#fff; text-align:center; padding:16px 24px; border-radius:8px; font-size:18px; font-weight:600; text-decoration:none; margin:24px 0; box-shadow: 0 4px 12px rgba(249,115,22,0.3); }}
.cta:hover {{ background:#ea580c; }}
.info {{ background:#f5f5f5; padding:16px; border-radius:8px; margin:16px 0; }}
.loja {{ font-size:14px; color:#666; text-transform:uppercase; letter-spacing:1px; }}
.cupom {{ background:#e8f5e9; border:2px dashed #4caf50; padding:12px; margin:16px 0; border-radius:8px; text-align:center; font-size:16px; font-weight:600; }}
footer {{ margin-top:32px; padding-top:16px; border-top:1px solid #ddd; color:#666; font-size:12px; text-align:center; }}
@media (prefers-color-scheme: dark) {{
  body {{ background:#111; color:#eee; }}
  .info {{ background:#1e1e1e; }}
}}
</style>
</head>
<body>
<article>
<div class="hero">
  {f'<img src="{h(foto)}" alt="{h(titulo[:100])}" loading="eager" fetchpriority="high">' if foto else ''}
</div>
<h1>{h(titulo)}</h1>
<p class="loja">📦 Via {loja}</p>
<div class="preco">
  R$ {preco:.2f}
  {f'<span class="preco-orig">R$ {preco_orig:.2f}</span>' if preco_orig else ''}
  {f'<span class="desconto">-{desconto}% OFF</span>' if desconto else ''}
</div>

{f'<div class="cupom">🎟️ CUPOM: <strong>{h(cupom)}</strong> — use na finalização</div>' if cupom else ''}

<a class="cta" href="{h(link)}" rel="sponsored nofollow" target="_blank">🛒 Ver Oferta Agora</a>

<div class="info">
  <h2 style="margin-top:0; font-size:16px;">🛡️ Sobre esta oferta</h2>
  <p>Esta oferta foi verificada em <strong>{agora}</strong>. Preços do {loja} podem mudar sem aviso. O botão acima leva ao produto oficial na loja.</p>
  <p>Categoria: <strong>{h(categoria)}</strong></p>
</div>

<footer>
  <p>© {datetime.now().year} Bot Ofertas — Publicidade. Compre com quem oferece garantia.</p>
  <p><a href="/">← Voltar às ofertas</a></p>
</footer>
</article>
</body>
</html>
"""

        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(html_page)
        return f"ofertas/{slug}.html"
    except Exception as e:
        log.warning("gerar_landing falhou: %s", e)
        return None


def gerar_index() -> None:
    """Atualiza web/index.html com últimas 60 ofertas."""
    try:
        from core import database as db  # noqa: PLC0415
        produtos = db.listar_todos(limite=60)
        cards = []
        for p in produtos:
            if p.get("status") != "enviado":
                continue
            titulo = p.get("titulo", "")
            foto = p.get("foto", "")
            preco = p.get("preco") or 0
            preco_orig = p.get("preco_original")
            desconto = p.get("desconto_pct") or 0
            if not desconto and preco_orig and preco:
                desconto = round((1 - preco / preco_orig) * 100)
            slug = f"{slugify(titulo, 60)}-{(p.get('id') or '')[:12]}".strip("-")
            h = html.escape
            cards.append(f"""
<a class="card" href="ofertas/{slug}.html">
  {f'<img src="{h(foto)}" alt="{h(titulo[:80])}" loading="lazy">' if foto else '<div class="no-img"></div>'}
  <div class="body">
    <h3>{h(titulo[:80])}</h3>
    <div class="p"><b>R$ {preco:.2f}</b> {f'<span class="off">-{desconto}%</span>' if desconto else ''}</div>
  </div>
</a>""")

        index_html = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot Ofertas — Melhores promoções do Mercado Livre e Amazon</title>
<meta name="description" content="Ofertas atualizadas diariamente com maiores descontos do Mercado Livre e Amazon Brasil. Preços verificados, cupons e frete grátis.">
<link rel="canonical" href="{SITE_URL}/">
<meta property="og:title" content="Bot Ofertas — Melhores promoções">
<meta property="og:description" content="Ofertas atualizadas diariamente">
<meta property="og:url" content="{SITE_URL}/">
<style>
body {{ font-family: system-ui,sans-serif; margin:0; padding:16px; background:#f9fafb; }}
header {{ text-align:center; padding:24px 0; }}
h1 {{ font-size:28px; margin:0; }}
.subtitle {{ color:#666; margin-top:8px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:16px; max-width:1200px; margin:0 auto; }}
.card {{ background:#fff; border:1px solid #e5e7eb; border-radius:12px; overflow:hidden; text-decoration:none; color:inherit; transition:transform 0.2s, box-shadow 0.2s; }}
.card:hover {{ transform:translateY(-2px); box-shadow:0 8px 24px rgba(0,0,0,0.1); }}
.card img {{ width:100%; height:200px; object-fit:contain; background:#fafafa; padding:8px; }}
.card .no-img {{ height:200px; background:#f0f0f0; }}
.card .body {{ padding:12px 16px; }}
.card h3 {{ font-size:14px; margin:0 0 8px; line-height:1.4; }}
.card .p {{ font-size:16px; }}
.card .p b {{ color:#c62828; }}
.card .off {{ display:inline-block; background:#c62828; color:#fff; padding:2px 8px; border-radius:4px; font-size:12px; margin-left:8px; }}
footer {{ text-align:center; padding:32px 16px; color:#666; font-size:12px; }}
</style>
</head>
<body>
<header>
  <h1>🔥 Bot Ofertas</h1>
  <p class="subtitle">Melhores promoções do dia — Mercado Livre e Amazon</p>
</header>
<main class="grid">
{"".join(cards)}
</main>
<footer>
  <p>© {datetime.now().year} Bot Ofertas — Publicidade. Todos os links são de afiliado.</p>
  <p><a href="/feed.xml">📡 RSS Feed</a></p>
</footer>
</body>
</html>
"""
        with open(os.path.join(WEB_DIR, "index.html"), "w", encoding="utf-8") as f:
            f.write(index_html)
    except Exception as e:
        log.warning("gerar_index falhou: %s", e)


def gerar_sitemap() -> None:
    """Gera web/sitemap.xml com todas as URLs de ofertas."""
    try:
        urls = [f"<url><loc>{SITE_URL}/</loc><changefreq>hourly</changefreq><priority>1.0</priority></url>"]
        if os.path.exists(OFERTAS_DIR):
            for arq in sorted(os.listdir(OFERTAS_DIR)):
                if arq.endswith(".html"):
                    urls.append(
                        f"<url><loc>{SITE_URL}/ofertas/{arq}</loc>"
                        f"<changefreq>daily</changefreq><priority>0.8</priority></url>"
                    )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(urls) + "</urlset>"
        )
        with open(os.path.join(WEB_DIR, "sitemap.xml"), "w", encoding="utf-8") as f:
            f.write(xml)

        with open(os.path.join(WEB_DIR, "robots.txt"), "w", encoding="utf-8") as f:
            f.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")
    except Exception as e:
        log.warning("gerar_sitemap falhou: %s", e)


def gerar_tudo(produto: dict) -> None:
    """Gera landing + atualiza index + sitemap. Chamado após cada publicação."""
    gerar_landing(produto)
    gerar_index()
    gerar_sitemap()
