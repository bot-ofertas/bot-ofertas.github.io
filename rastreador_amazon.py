# -*- coding: utf-8 -*-
"""
RASTREADOR DE CUPONS AMAZON
============================
Busca cupons e promoções na Amazon Brasil, gera links com tag de afiliado
e publica no Telegram com banner "ALERTA CUPOM".

Pré-requisito:
    AMAZON_AFFILIATE_TAG=seu-tag-20  ← no .env ou GitHub Secrets

Como usar:
    python rastreador_amazon.py           → roda uma vez
    python rastreador_amazon.py --loop 120 → a cada 2 horas
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import argparse
import asyncio
import logging
import os
import time

from dotenv import load_dotenv
from telegram import Bot

import core.database as db
from core.scorer import calcular_score
from core.validador import validar
from integrations.amazon_scraper import buscar_cupons_amazon_async, amazon_ativo
from integrations.telegram_bot import publicar_alerta_cupom

load_dotenv()

TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "")
CANAIS = {"geral": os.getenv("CANAL_GERAL", "")}

MAX_POR_EXECUCAO = 3   # máximo de posts por rodada (Amazon é mais restrito)
DESCONTO_MIN     = 10  # cupons sem desconto calculável passam mesmo assim
PAUSA_ENTRE_POSTS = 8  # segundos (Amazon é mais sensível a spam)
SCORE_MINIMO     = 40  # threshold menor pois cupons têm valor extra intrínseco

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

def log(msg: str) -> None:
    print(msg)
    logging.info(msg)


def _id_amazon(produto: dict) -> str:
    """ID estável baseado na ASIN (parte /dp/ASIN da URL)."""
    link = produto.get("link", "")
    m = __import__("re").search(r"/dp/([A-Z0-9]{10})", link)
    return m.group(1) if m else link.split("?")[0][-20:]


async def rodar_uma_vez() -> None:
    if not amazon_ativo():
        print("❌ AMAZON_AFFILIATE_TAG não configurada.")
        print("   Adicione ao .env: AMAZON_AFFILIATE_TAG=seu-tag-20")
        print("   Crie sua conta em: https://associados.amazon.com.br/")
        return

    if not TOKEN_TELEGRAM:
        print("❌ TOKEN_TELEGRAM não definido.")
        return

    db.inicializar()

    log("\n" + "=" * 55)
    log("Rastreador Amazon Cupons iniciado")

    produtos = await buscar_cupons_amazon_async(
        desconto_min=DESCONTO_MIN, limite=20
    )
    log(f"  {len(produtos)} produto(s) encontrado(s) na Amazon Brasil")

    com_cupom = sum(1 for p in produtos if p.get("cupom"))
    log(f"  {com_cupom} com cupom de desconto")

    publicados = 0
    async with Bot(token=TOKEN_TELEGRAM) as bot:
        for item in produtos:
            if publicados >= MAX_POR_EXECUCAO:
                break

            produto_id = _id_amazon(item)
            item["id"] = produto_id

            # Registra histórico de preço
            db.registrar_preco(produto_id, item.get("preco"))

            # Deduplicação
            url_base = item.get("link", "").split("?")[0]
            if db.link_ja_existe(url_base):
                log(f"  ↩️  Duplicata: {item['titulo'][:50]}")
                continue

            # Validação anti-golpe (ajustada — cupons Amazon têm preço base real)
            aprovado, motivo = validar(item, reputacao={})
            if not aprovado:
                # Para cupons Amazon, rejeita só se for desconto impossível (>90%)
                if "desconto irreal" not in motivo.lower():
                    log(f"  ⚠️  Rejeitado [{motivo}]: {item['titulo'][:50]}")
                    continue

            # Score
            score = calcular_score(item)
            # Bônus por ter cupom
            if item.get("cupom"):
                score = min(100, score + 15)
            item["score"] = score

            if score < SCORE_MINIMO:
                log(f"  📊 Score {score} < {SCORE_MINIMO}: {item['titulo'][:50]}")
                continue

            cupom_info = f" [cupom: {item['cupom']}]" if item.get("cupom") else ""
            log(f"  ✅ {item['titulo'][:50]} | {item.get('desconto_pct', 0):.0f}% OFF{cupom_info}")

            sucesso = await publicar_alerta_cupom(bot, item, CANAIS)
            if sucesso:
                item["status"] = "enviado"
                item["adicionado_em"] = __import__("datetime").datetime.now().isoformat()
                item["affiliate_link"] = item.get("link", "")
                db.inserir_produto(item)
                db.marcar_enviado(produto_id)
                publicados += 1
                log(f"  📤 Publicado! ({publicados}/{MAX_POR_EXECUCAO})")
                await asyncio.sleep(PAUSA_ENTRE_POSTS)

    log(f"\n{'=' * 55}")
    log(f"Amazon: {publicados} cupom(s) publicado(s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rastreador de cupons Amazon Brasil")
    parser.add_argument("--loop", type=int, metavar="MINUTOS")
    args = parser.parse_args()

    if args.loop:
        log(f"Modo contínuo: a cada {args.loop} min. Ctrl+C para parar.")
        while True:
            asyncio.run(rodar_uma_vez())
            log(f"\n⏳ Próxima rodada em {args.loop} minuto(s)...")
            time.sleep(args.loop * 60)
    else:
        asyncio.run(rodar_uma_vez())


if __name__ == "__main__":
    main()
