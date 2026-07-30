# -*- coding: utf-8 -*-
"""
CAMPANHA DEDICADA — Ferramentas para Construção Civil
========================================================
Busca ofertas de ferramentas (parafusadeiras, níveis a laser, malas de
ferramentas, furadeiras etc.) nas categorias Ferramentas + Construção do
Mercado Livre e publica pelo menos 6 por rodada, a cada 15 minutos.

Roda como processo separado do rastreador.py principal — MAX_POR_CATEGORIA=1
existe lá pra garantir DIVERSIDADE entre muitas categorias por rodada; essa
campanha é o oposto por natureza (volume alto numa única área), então usa
sua própria lógica de publicação em vez de reusar processar_categoria().

Regra de ouro igual ao resto do projeto: só publica com link oficial de
afiliado gerado (matt_tool=47114387).

Como usar:
    python campanha_ferramentas.py            → roda uma vez agora
    python campanha_ferramentas.py --loop 15  → a cada 15 minutos, contínuo
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from telegram import Bot

from core.scorer import score_inteligente
from core.validador import validar
import core.database as db
from affiliates.registry import get_provider
from integrations.ml_browser import (
    _extrair_produtos_json,
    _normalizar_dom,
    _DOM_SCRIPT,
    _filtrar_e_afiliar,
)
from integrations.telegram_bot import publicar, publicar_alerta_cupom
from integrations.social_poster import publicar_todas_redes, resumo_redes
from integrations.whatsapp_sender import enviar_para_grupo, wa_ativo

load_dotenv()

TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "")
CANAIS = {"geral": os.getenv("CANAL_GERAL", "")}

MIN_POR_RODADA = 6
DESCONTO_MINIMO = 10
SCORE_MINIMO = 50  # um pouco mais permissivo que o padrão (60) pra sustentar volume
PAUSA_ENTRE_POSTS = 6

# Ferramentas em si + Construção (tem parafusadeiras/furadeiras/esmerilhadeiras
# de marcas como Bosch também, mas mistura com fechadura/cuba/luminária —
# por isso o filtro de palavra-chave abaixo).
URLS_FONTE = [
    ("ferramentas", "https://www.mercadolivre.com.br/c/ferramentas"),
    ("construcao", "https://www.mercadolivre.com.br/c/construcao"),
]

_PALAVRAS_FERRAMENTA = (
    "parafusadeira", "furadeira", "esmerilhadeira", "nivel", "nível", "laser",
    "maleta", "chave de fenda", "chave allen", "jogo de chave", "martelo",
    "serra ", "serra-", "alicate", "trena", "broca", "lixadeira",
    "kit ferramenta", "caixa de ferramenta", "furadeira de impacto",
    "parafusadeira e furadeira", "makita", "bosch", "dewalt", "vonder",
    "tramontina", "the black tools", "wap",
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def log(msg: str) -> None:
    print(msg)
    logging.info(msg)


def _id_produto(item: dict) -> str:
    url = item.get("link", "")
    return url.split("?")[0].rstrip("/").split("/")[-1] or url[:60]


def _e_ferramenta(item: dict) -> bool:
    titulo = (item.get("titulo") or "").lower()
    return any(p in titulo for p in _PALAVRAS_FERRAMENTA)


async def _buscar_pagina(url: str) -> list[dict]:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="pt-BR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 1024},
        )
        try:
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=40000)
            except PWTimeout:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                await page.wait_for_selector(
                    ".andes-card.poly-card, [class*='poly-card--grid']", timeout=10000
                )
            except PWTimeout:
                pass
            html = await page.content()
            produtos = _extrair_produtos_json(html)
            if not produtos:
                raw = await page.evaluate(_DOM_SCRIPT)
                produtos = _normalizar_dom(raw)
            return produtos
        finally:
            # Mesma proteção contra vazamento de chromium.exe do resto do projeto.
            try:
                await browser.close()
            except Exception:
                pass


async def rodar_uma_vez() -> int:
    db.inicializar()
    if not TOKEN_TELEGRAM:
        log("❌ TOKEN_TELEGRAM não definido no .env")
        return 0

    log("\n" + "=" * 55)
    log(f"Campanha Ferramentas — {datetime.now().strftime('%H:%M:%S')}")

    candidatos: list[dict] = []
    for nicho, url in URLS_FONTE:
        try:
            brutos = await _buscar_pagina(url)
        except Exception as e:
            log(f"  ⚠️  Erro buscando {nicho}: {e}")
            db.registrar_erro("scraping", str(e))
            continue
        filtrados = _filtrar_e_afiliar(brutos, nicho, DESCONTO_MINIMO, limite=50)
        relevantes = [i for i in filtrados if _e_ferramenta(i)]
        log(f"  {nicho}: {len(brutos)} bruto(s) → {len(filtrados)} c/ desconto → {len(relevantes)} de ferramenta")
        candidatos.extend(relevantes)

    # Ordena por desconto (melhores primeiro) e remove duplicatas entre as fontes
    vistos: set[str] = set()
    unicos = []
    for item in sorted(candidatos, key=lambda i: i.get("desconto_pct", 0), reverse=True):
        pid = _id_produto(item)
        if pid in vistos:
            continue
        vistos.add(pid)
        unicos.append(item)

    publicados = 0
    async with Bot(token=TOKEN_TELEGRAM) as bot:
        for item in unicos:
            if publicados >= MIN_POR_RODADA:
                break

            produto_id = _id_produto(item)
            item["id"] = produto_id
            titulo_curto = (item.get("titulo") or "")[:55]

            try:
                db.registrar_preco(produto_id, item.get("preco"))

                if db.produto_id_existe(produto_id):
                    continue

                aprovado, motivo = validar(item, reputacao={})
                if not aprovado:
                    continue

                score = score_inteligente(item)
                item["score"] = score
                if score < SCORE_MINIMO:
                    continue

                url_original = item.get("link", "").split("?")[0]
                provider = get_provider(url_original)
                if provider is None:
                    continue

                try:
                    link_afiliado = await provider.generate_affiliate_link_async(url_original)
                except Exception as e:
                    log(f"     ❌ Erro ao gerar link: {e}")
                    db.registrar_erro("affiliate", str(e), produto_id)
                    continue

                if not link_afiliado or not provider.validate_affiliate_link(link_afiliado):
                    item["status"] = "pendente"
                    item["adicionado_em"] = datetime.now().isoformat()
                    db.inserir_produto(item)
                    db.atualizar_afiliado(produto_id, provider.name, "", "erro")
                    continue

                item["link"] = link_afiliado
                item["categoria"] = "ferramentas"

                tem_cupom = bool(item.get("cupom"))
                if tem_cupom:
                    sucesso = await publicar_alerta_cupom(bot, item, CANAIS)
                else:
                    sucesso = await publicar(bot, item, CANAIS)

                if not sucesso:
                    db.registrar_erro("telegram", "falha ao publicar", produto_id)
                    continue

                item["status"] = "enviado"
                item["adicionado_em"] = datetime.now().isoformat()
                db.inserir_produto(item)
                db.atualizar_afiliado(produto_id, provider.name, link_afiliado, "ok")
                db.marcar_enviado(produto_id)
                publicados += 1
                log(f"  ✅ ({publicados}/{MIN_POR_RODADA}) {titulo_curto} | {item.get('desconto_pct', 0):.0f}% OFF")

                if wa_ativo():
                    try:
                        wa_ok = await asyncio.wait_for(enviar_para_grupo(item), timeout=90.0)
                        log(f"     💚 WhatsApp: {'enviado' if wa_ok else 'falhou'}")
                    except Exception as e:
                        from core.error_logger import log_erro
                        log_erro("wa.envio_falha", e, {"produto_id": produto_id})
                        log(f"     ⚠️  WhatsApp: {e}")

                try:
                    redes = await publicar_todas_redes(item)
                    if redes:
                        log(f"     🌐 Redes: {resumo_redes(redes)}")
                except Exception as e:
                    log(f"     ⚠️  Social: {e}")

                await asyncio.sleep(PAUSA_ENTRE_POSTS)
            except Exception as e_item:
                log(f"  ⚠️  Erro inesperado em '{titulo_curto}': {e_item}")
                db.registrar_erro("item_falhou", str(e_item), produto_id)
                continue

    log(f"\nRodada concluída: {publicados} publicado(s) (meta: {MIN_POR_RODADA})")
    if publicados < MIN_POR_RODADA:
        log(
            "  ℹ️  Estoque de ofertas de ferramenta com desconto real disponível agora "
            "é menor que a meta — publicou o que havia sem repetir nem forçar itens fracos."
        )
    return publicados


def main() -> None:
    parser = argparse.ArgumentParser(description="Campanha de ofertas de ferramentas para construção civil")
    parser.add_argument("--loop", type=int, metavar="MINUTOS", help="Roda em loop a cada N minutos (ex: --loop 15)")
    args = parser.parse_args()

    if args.loop:
        log(f"Modo contínuo: a cada {args.loop} minuto(s). Ctrl+C para parar.")
        while True:
            try:
                asyncio.run(rodar_uma_vez())
            except Exception as e:
                log(f"⚠️  Rodada falhou inesperadamente: {e}")
                db.registrar_erro("campanha_ferramentas_falhou", str(e))
            log(f"\n⏳ Próxima rodada em {args.loop} minuto(s)...")
            time.sleep(args.loop * 60)
    else:
        asyncio.run(rodar_uma_vez())


if __name__ == "__main__":
    main()
