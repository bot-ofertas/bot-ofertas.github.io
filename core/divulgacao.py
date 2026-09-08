# -*- coding: utf-8 -*-
"""
DIVULGAÇÃO — gera anúncios prontos dos grupos e das ofertas, por rede.

Objetivo (pedido do Daniel): "como posso divulgar também os produtos do
grupo de ofertas, gerar anúncios diretamente em redes sociais para promover
o grupo... preciso que as pessoas entrem no grupo e comprem pelo meu link de
afiliado."

Este módulo é a parte *funcional* da divulgação: ele lê ofertas REAIS do
banco (as mesmas já validadas, com link de afiliado conferido) e devolve o
texto pronto por rede, com:
  - CTA de entrada nos dois grupos em TODA postagem;
  - `matt_source`/`ascsubtag` trocado conforme a rede (core.tracking), pra
    saber de qual canal veio a venda;
  - UTM na página ponte, pra medir quem entrou no grupo por qual anúncio.

Quem publica em horário é o n8n (workflow `04-divulgacao-social`), que
chama `GET /divulgacao?rede=instagram` no healthcheck e manda o resultado
pro canal. Assim o "calendário" não é uma tela — é agendamento de verdade,
rodando na nuvem mesmo com o PC do Daniel desligado.

Uso direto:
    python -m core.divulgacao instagram
"""
from __future__ import annotations

import os
import random
from datetime import datetime

from core.tracking import link_utm, marcar_origem

# Grupos oficiais (informados pelo Daniel em 28/06). Sobrescrevíveis no .env
# — os defaults existem pra que a divulgação funcione mesmo num ambiente
# recém-clonado, sem configuração nenhuma.
GRUPO_TELEGRAM = os.getenv("GRUPO_TELEGRAM_URL", "https://t.me/ofertaseletronics")
GRUPO_WHATSAPP = os.getenv(
    "GRUPO_WHATSAPP_URL", "https://chat.whatsapp.com/JyJ9uLoZdE5LboH9GjAooC"
)
SITE_URL = os.getenv("SITE_URL", "https://bot-ofertas.github.io/")
PAGINA_PONTE = SITE_URL.rstrip("/") + "/grupos/"

REDES = ("instagram", "facebook", "tiktok", "twitter", "telegram", "whatsapp", "youtube")

HASHTAGS = {
    "instagram": "#ofertas #promoção #achadinhos #descontos #mercadolivre #tecnologia",
    "facebook":  "#ofertas #promoções #descontos",
    "tiktok":    "#ofertas #achadinhos #promocao #tecnologia #fyp",
    "twitter":   "#promoção #oferta #desconto",
    "youtube":   "#shorts #ofertas #promocao",
}

_ABERTURAS = (
    "🔥 Achado do dia",
    "⚡ Caiu de preço",
    "💥 Oferta relâmpago",
    "🎯 Baixou agora",
)


def _brl(valor: float | None) -> str:
    """R$ 1.234,56 — formatação brasileira sem depender de locale do SO
    (o locale pt_BR não vem instalado no runner do GitHub Actions)."""
    if valor is None:
        return "—"
    return "R$ " + f"{float(valor):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def rodape_grupos(rede: str = "site") -> str:
    """CTA de entrada nos grupos — vai no fim de TODA postagem gerada."""
    tg = link_utm(GRUPO_TELEGRAM, origem=rede, conteudo="cta_rodape")
    wa = link_utm(GRUPO_WHATSAPP, origem=rede, conteudo="cta_rodape")
    return (
        "Quer receber ofertas assim todos os dias?\n"
        f"📢 Telegram: {tg}\n"
        f"📲 WhatsApp: {wa}"
    )


def texto_oferta(produto: dict, rede: str = "instagram", com_link: bool = True) -> str:
    """Post de UMA oferta, no formato aprovado pelo Daniel (28/06)."""
    titulo = (produto.get("titulo") or "Oferta").strip()
    preco = produto.get("preco")
    original = produto.get("preco_original")
    desconto = produto.get("desconto_pct") or 0
    economia = (original - preco) if (original and preco and original > preco) else None
    link = produto.get("affiliate_link") or produto.get("link") or ""
    link_rastreado = marcar_origem(link, rede) if link else ""

    linhas = [f"{random.choice(_ABERTURAS)}: {titulo}"]
    if preco:
        linhas.append(f"💰 {_brl(preco)}" + (f" (de {_brl(original)})" if original else ""))
    if desconto:
        linhas.append(f"✅ {desconto:.0f}% OFF")
    if economia:
        linhas.append(f"💸 Economia de {_brl(economia)}")
    if com_link and link_rastreado:
        linhas.append(f"\n🛒 Comprar:\n{link_rastreado}")
    linhas.append("\n⚠️ Preço sujeito a alteração pela loja.")
    linhas.append("")
    linhas.append(rodape_grupos(rede))
    if HASHTAGS.get(rede):
        linhas.append("")
        linhas.append(HASHTAGS[rede])
    return "\n".join(linhas)


def texto_grupo(rede: str = "instagram") -> str:
    """Post de divulgação PURA do grupo (sem produto específico)."""
    ponte = link_utm(PAGINA_PONTE, origem=rede, conteudo="post_grupo")
    corpo = (
        "📦 Ofertas de eletrônicos, celulares e informática garimpadas todo dia.\n"
        "✅ Só promoção real: o preço é conferido contra o histórico antes de ir pro grupo.\n"
        "🚫 Sem spam — só oferta boa.\n\n"
        f"👉 Entre por aqui: {ponte}\n\n"
        + rodape_grupos(rede)
    )
    if HASHTAGS.get(rede):
        corpo += "\n\n" + HASHTAGS[rede]
    return corpo


def top_ofertas(limite: int = 3, score_minimo: int = 60) -> list[dict]:
    """Melhores ofertas JÁ PUBLICADAS (portanto já validadas e com link de
    afiliado conferido) — nunca inventa produto pra divulgação."""
    try:
        import core.database as db  # noqa: PLC0415
        produtos = [
            p for p in db.listar_todos(limite=200)
            if p.get("status") == "enviado"
            and (p.get("score") or 0) >= score_minimo
            and p.get("affiliate_link")
            and p.get("foto")
        ]
    except Exception:
        return []
    produtos.sort(key=lambda p: (p.get("desconto_pct") or 0, p.get("score") or 0), reverse=True)
    return produtos[:limite]


def carrossel_do_dia(rede: str = "instagram", quantidade: int = 3) -> dict:
    """"N ofertas do dia" — um post único com as melhores do banco.

    Devolve {"rede", "texto", "fotos", "links", "gerado_em"}. Sem ofertas
    no banco, cai no post de divulgação do grupo em vez de devolver vazio:
    a divulgação nunca deve ficar um dia sem conteúdo por causa de uma
    rodada de scraping fraca.
    """
    ofertas = top_ofertas(quantidade)
    if not ofertas:
        return {
            "rede": rede,
            "texto": texto_grupo(rede),
            "fotos": [],
            "links": [],
            "tipo": "grupo",
            "gerado_em": datetime.now().isoformat(timespec="seconds"),
        }

    cabecalho = f"🔥 {len(ofertas)} ofertas de hoje no grupo:\n"
    blocos, fotos, links = [], [], []
    for i, p in enumerate(ofertas, 1):
        link = marcar_origem(p.get("affiliate_link") or p.get("link") or "", rede)
        desconto = p.get("desconto_pct") or 0
        blocos.append(
            f"{i}️⃣ {(p.get('titulo') or '')[:70]}\n"
            f"   {_brl(p.get('preco'))}"
            + (f" — {desconto:.0f}% OFF" if desconto else "")
            + (f"\n   {link}" if link else "")
        )
        if p.get("foto"):
            fotos.append(p["foto"])
        if link:
            links.append(link)

    texto = "\n\n".join([cabecalho, *blocos, rodape_grupos(rede)])
    if HASHTAGS.get(rede):
        texto += "\n\n" + HASHTAGS[rede]
    return {
        "rede": rede,
        "texto": texto,
        "fotos": fotos,
        "links": links,
        "tipo": "carrossel",
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
    }


def gerar(rede: str = "instagram", tipo: str = "auto", quantidade: int = 3) -> dict:
    """Ponto de entrada usado pelo healthcheck (`GET /divulgacao`) e pelo n8n.

    tipo: "auto" (carrossel se houver oferta, senão grupo) | "grupo" |
          "carrossel" | "oferta" (a melhor oferta isolada).
    """
    rede = rede if rede in REDES else "instagram"
    if tipo == "grupo":
        return {
            "rede": rede, "tipo": "grupo", "texto": texto_grupo(rede),
            "fotos": [], "links": [],
            "gerado_em": datetime.now().isoformat(timespec="seconds"),
        }
    if tipo == "oferta":
        melhores = top_ofertas(1)
        if melhores:
            p = melhores[0]
            return {
                "rede": rede, "tipo": "oferta", "texto": texto_oferta(p, rede),
                "fotos": [p["foto"]] if p.get("foto") else [],
                "links": [marcar_origem(p.get("affiliate_link") or p.get("link") or "", rede)],
                "gerado_em": datetime.now().isoformat(timespec="seconds"),
            }
    return carrossel_do_dia(rede, quantidade)


if __name__ == "__main__":  # pragma: no cover
    import sys
    rede_cli = sys.argv[1] if len(sys.argv) > 1 else "instagram"
    tipo_cli = sys.argv[2] if len(sys.argv) > 2 else "auto"
    resultado = gerar(rede_cli, tipo_cli)
    print(f"── {resultado['rede']} / {resultado['tipo']} " + "─" * 30)
    print(resultado["texto"])
