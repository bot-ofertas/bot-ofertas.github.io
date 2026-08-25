# -*- coding: utf-8 -*-
"""Script de uso unico: reenvia o giro curado de Smart TVs 40-43" ate
R$1700 com desconto, pedido pelo Daniel. Itens ja estavam 'enviado' no
banco (nao sao novidade) -- reenvio deliberado, nao passa pelo dedup
normal do rastreador."""
import asyncio
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from telegram import Bot
from integrations.telegram_bot import publicar
from integrations.whatsapp_sender import enviar_para_grupo, wa_ativo

TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "")
CANAIS = {"geral": os.getenv("CANAL_GERAL", "")}


async def main():
    with open("data/_lote_tvs.json", encoding="utf-8") as f:
        produtos = json.load(f)

    print(f"Enviando {len(produtos)} Smart TVs (giro curado)...")

    async with Bot(token=TOKEN_TELEGRAM) as bot:
        for i, p in enumerate(produtos, 1):
            item = {
                "titulo": p["titulo"],
                "preco": p["preco"],
                "preco_original": p.get("preco_original"),
                "desconto_pct": p.get("desconto_pct"),
                "link": p["affiliate_link"],
                "foto": p.get("foto"),
                "cupom": p.get("cupom"),
                "fonte": p.get("affiliate_provider") or "ml",
                "canal": "geral",
            }
            print(f"[{i}/{len(produtos)}] {p['id']} — {p['titulo'][:50]}")

            ok_tg = await publicar(bot, item, CANAIS)
            print(f"   Telegram: {'OK' if ok_tg else 'FALHOU'}")

            if wa_ativo():
                try:
                    ok_wa = await asyncio.wait_for(enviar_para_grupo(item), timeout=90.0)
                    print(f"   WhatsApp: {'OK' if ok_wa else 'falhou'}")
                except asyncio.TimeoutError:
                    print("   WhatsApp: timeout (>90s)")

            if i < len(produtos):
                time.sleep(8)

    print("\nConcluído.")


asyncio.run(main())
