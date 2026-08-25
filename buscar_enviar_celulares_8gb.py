# -*- coding: utf-8 -*-
"""Script de uso único: busca ao vivo na categoria 'celulares' do ML por
aparelhos com 8GB+ RAM até R$2500 com desconto real, e envia os melhores
achados para Telegram + WhatsApp. Pedido pelo Daniel em 2026-08-18."""
import asyncio
import json
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

import os
from core.error_logger import setup_logging
setup_logging()
from telegram import Bot
from integrations.telegram_bot import publicar
from integrations.whatsapp_sender import enviar_para_grupo, wa_ativo
from integrations.ml_browser import buscar_ofertas_browser

TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "")
CANAIS = {"geral": os.getenv("CANAL_GERAL", "")}

PRECO_MAX = 2500.0
RAM_MIN = 8
DESCONTO_MIN = 15.0

_RE_RAM = re.compile(r"(\d+)\s*GB\s*(?:DE\s*)?RAM", re.IGNORECASE)
_ACESSORIO_KEYWORDS = (
    "capa", "capinha", "pelicula", "película", "fone", "carregador",
    "cabo usb", "suporte", "case ", "adaptador", "power bank", "bateria externa",
)


def _extrair_ram(titulo: str) -> int | None:
    m = _RE_RAM.search(titulo)
    return int(m.group(1)) if m else None


def _parece_acessorio(titulo: str) -> bool:
    t = titulo.lower()
    return any(k in t for k in _ACESSORIO_KEYWORDS)


def main_busca():
    print("Buscando ofertas na categoria celulares...")
    produtos = buscar_ofertas_browser("celulares", desconto_min=5, limite=100)
    print(f"{len(produtos)} produtos brutos retornados da pagina de ofertas.")

    candidatos = []
    for p in produtos:
        titulo = p.get("titulo", "")
        preco = p.get("preco")
        desconto = p.get("desconto_pct", 0)
        if not titulo or not preco:
            continue
        if _parece_acessorio(titulo):
            continue
        ram = _extrair_ram(titulo)
        if ram is None or ram < RAM_MIN:
            continue
        if preco > PRECO_MAX:
            continue
        if not p.get("preco_original") or desconto < DESCONTO_MIN:
            continue
        p["ram_detectada"] = ram
        candidatos.append(p)

    candidatos.sort(key=lambda p: (p.get("mais_vendido", False), p["desconto_pct"], p["preco"]), reverse=True)
    return candidatos


# Curadoria "topo de linha": entre os candidatos filtrados, prioriza
# linhas premium/flagship-adjacent (Galaxy A5x, Motorola Edge, gamer
# alto-RAM) em vez de simplesmente pegar quem tem maior desconto —
# TCL 60 e Realme C73, por exemplo, batem no filtro mas sao entry-level.
_PALAVRAS_TOPO_LINHA = ("galaxy a5", "edge 60", "gt30 pro")


def selecionar_topo_linha(candidatos: list[dict], limite: int = 4) -> list[dict]:
    escolhidos = [
        p for p in candidatos
        if any(kw in p["titulo"].lower() for kw in _PALAVRAS_TOPO_LINHA)
    ]
    return escolhidos[:limite]


async def enviar(candidatos: list[dict]):
    print(f"\nEnviando {len(candidatos)} ofertas (topo de linha, 8GB+ RAM, ate R${PRECO_MAX:.0f})...")
    async with Bot(token=TOKEN_TELEGRAM) as bot:
        for i, p in enumerate(candidatos, 1):
            print(f"[{i}/{len(candidatos)}] {p['titulo'][:60]} | R${p['preco']:.2f}")
            ok_tg = await publicar(bot, p, CANAIS)
            print(f"   Telegram: {'OK' if ok_tg else 'FALHOU'}")

            if wa_ativo():
                try:
                    ok_wa = await asyncio.wait_for(enviar_para_grupo(p), timeout=90.0)
                    print(f"   WhatsApp: {'OK' if ok_wa else 'falhou'}")
                except asyncio.TimeoutError:
                    print("   WhatsApp: timeout (>90s)")

            if i < len(candidatos):
                time.sleep(8)
    print("\nConcluido.")


if __name__ == "__main__":
    candidatos = main_busca()
    print(f"\n{len(candidatos)} candidatos apos filtro (RAM>={RAM_MIN}GB, preco<=R${PRECO_MAX:.0f}, desconto>={DESCONTO_MIN}%):\n")
    for p in candidatos:
        print(f"- [{p['ram_detectada']}GB RAM] {p['titulo'][:70]} | R${p['preco']:.2f} (de R${p['preco_original']:.2f}, -{p['desconto_pct']}%) | selo={p.get('selo_ml','')}")

    with open("data/_candidatos_celulares.json", "w", encoding="utf-8") as f:
        json.dump(candidatos, f, ensure_ascii=False, indent=2)
    print("\nSalvo em data/_candidatos_celulares.json")

    escolhidos = selecionar_topo_linha(candidatos)
    print(f"\n{len(escolhidos)} selecionados como 'topo de linha':")
    for p in escolhidos:
        print(f"  - {p['titulo'][:70]} | R${p['preco']:.2f} (-{p['desconto_pct']}%)")

    if escolhidos:
        asyncio.run(enviar(escolhidos))
    else:
        print("Nenhum candidato bateu os criterios de 'topo de linha' — nada enviado.")
