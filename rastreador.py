# -*- coding: utf-8 -*-
"""
RASTREADOR AUTOMÁTICO DE OFERTAS
==================================
Busca produtos em promoção na API do Mercado Livre, valida contra golpes,
calcula score de oferta + demanda e publica automaticamente no Telegram.

Como usar:
    python rastreador.py              → roda uma vez agora
    python rastreador.py --loop 60   → roda a cada 60 minutos continuamente

Ou configure no Agendador de Tarefas do Windows (veja 4_agendar.bat).
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import argparse
import asyncio
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from telegram import Bot

from core.scorer import calcular_score
from core.validador import validar, score_demanda
from core.deduplicator import e_duplicata, registrar_envio
from core.scheduler import e_bom_momento, resumo_horario
from integrations.mercadolivre import buscar_ofertas, obter_reputacao_vendedor, CATEGORIAS_ML
from integrations.telegram_bot import publicar

load_dotenv()

TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "")
CANAIS = {"geral": os.getenv("CANAL_GERAL", "")}

# ── Configuração ──────────────────────────────────────────────────────────────
CATEGORIAS_ATIVAS = ["celulares", "eletronicos", "informatica"]  # quais monitorar
DESCONTO_MINIMO   = 15    # % mínimo de desconto real
SCORE_MINIMO      = 55    # score combinado (oferta + demanda) para publicar
MAX_POR_EXECUCAO  = 5     # máximo de posts por rodada (evita spam no canal)
PAUSA_ENTRE_POSTS = 10    # segundos entre cada envio
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    filename="rastreador.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    encoding="utf-8",
)


def log(msg: str) -> None:
    print(msg)
    logging.info(msg)


async def processar_categoria(
    bot: Bot,
    nome: str,
    cat_id: str,
    publicados: list[int],
) -> None:
    if publicados[0] >= MAX_POR_EXECUCAO:
        return

    log(f"\n🔍 [{nome}] buscando ofertas...")
    try:
        itens = buscar_ofertas(cat_id, desconto_min=DESCONTO_MINIMO)
    except RuntimeError as e:
        log(f"  ❌ {e}")
        return

    log(f"  {len(itens)} produto(s) com desconto ≥{DESCONTO_MINIMO}%")

    for item in itens:
        if publicados[0] >= MAX_POR_EXECUCAO:
            break

        titulo_curto = item["titulo"][:55] + "…" if len(item["titulo"]) > 55 else item["titulo"]

        # 1. Duplicata
        if e_duplicata(item):
            continue

        # 2. Validação anti-golpe
        rep = obter_reputacao_vendedor(item["vendedor_id"])
        aprovado, motivo = validar(item, rep)
        if not aprovado:
            log(f"  ⚠️  Rejeitado — {motivo}: {titulo_curto}")
            continue

        # 3. Score combinado
        s_oferta  = calcular_score(item)
        s_demanda = score_demanda(item)
        score     = int(s_oferta * 0.5 + s_demanda * 0.5)
        item["score"] = score

        vendas = item.get("quantidade_vendida", 0)
        log(
            f"  📊 {titulo_curto}\n"
            f"     {item['desconto_pct']}% OFF | {vendas} vendas | "
            f"score {score} (oferta {s_oferta} + demanda {s_demanda})"
        )

        if score < SCORE_MINIMO:
            log(f"     → score abaixo do mínimo ({SCORE_MINIMO}), pulando")
            continue

        # 4. Publicar
        sucesso = await publicar(bot, item, CANAIS)
        if sucesso:
            registrar_envio(item)
            publicados[0] += 1
            log(f"  ✅ Publicado! ({publicados[0]}/{MAX_POR_EXECUCAO} desta rodada)")
            await asyncio.sleep(PAUSA_ENTRE_POSTS)


async def rodar_uma_vez() -> None:
    if not TOKEN_TELEGRAM:
        print("❌ TOKEN_TELEGRAM não definido no .env")
        return

    log("\n" + "=" * 55)
    log(f"Rastreador iniciado — {resumo_horario()}")

    if not e_bom_momento():
        log("⏰ Horário não ideal para engajamento, mas prosseguindo...")

    publicados: list[int] = [0]

    async with Bot(token=TOKEN_TELEGRAM) as bot:
        for nome in CATEGORIAS_ATIVAS:
            cat_id = CATEGORIAS_ML.get(nome)
            if cat_id:
                await processar_categoria(bot, nome, cat_id, publicados)

    log(f"\n{'='*55}")
    log(f"Rodada concluída — {publicados[0]} produto(s) publicado(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rastreador de ofertas ML")
    parser.add_argument(
        "--loop", type=int, metavar="MINUTOS",
        help="Rodar em loop a cada N minutos (ex: --loop 60)"
    )
    args = parser.parse_args()

    if args.loop:
        log(f"Modo contínuo: rodando a cada {args.loop} minuto(s). Ctrl+C para parar.")
        while True:
            asyncio.run(rodar_uma_vez())
            log(f"\n⏳ Próxima rodada em {args.loop} minuto(s)...")
            time.sleep(args.loop * 60)
    else:
        asyncio.run(rodar_uma_vez())


if __name__ == "__main__":
    main()
