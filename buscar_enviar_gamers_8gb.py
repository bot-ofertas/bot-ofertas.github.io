# -*- coding: utf-8 -*-
"""Script de uso único: busca celulares GAMER topo de linha (sem Motorola,
8GB+ RAM, ate R$2500, desconto real) e envia para Telegram + WhatsApp.
Pedido pelo Daniel em 2026-08-18, refinando o envio anterior de celulares
(que incluia Motorola) so pra linha gamer."""
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
from integrations.ml_browser import (
    _extrair_produtos_json, _normalizar_dom, _DOM_SCRIPT, _UA, _filtrar_e_afiliar,
)

TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "")
CANAIS = {"geral": os.getenv("CANAL_GERAL", "")}

PRECO_MAX = 2500.0
RAM_MIN = 8
DESCONTO_MIN = 15.0

_RE_RAM = re.compile(r"(\d+)\s*GB\s*(?:DE\s*)?RAM", re.IGNORECASE)
_GAMER_KEYWORDS = (
    "gamer", "gaming", "poco", "rog phone", "realme gt", "infinix gt",
    "iqoo", "black shark", "redmi note.*pro",
)
_ACESSORIO_KEYWORDS = (
    "capa", "capinha", "pelicula", "película", "fone", "carregador",
    "cabo usb", "suporte", "case ", "adaptador", "power bank", "bateria externa",
)

_URLS = [
    "https://www.mercadolivre.com.br/ofertas?category=MLB1051&q=gamer",
    "https://www.mercadolivre.com.br/ofertas?q=celular+gamer",
]


def _extrair_ram(titulo: str) -> int | None:
    m = _RE_RAM.search(titulo)
    return int(m.group(1)) if m else None


def _parece_acessorio(titulo: str) -> bool:
    t = titulo.lower()
    return any(k in t for k in _ACESSORIO_KEYWORDS)


def _e_gamer(titulo: str) -> bool:
    t = titulo.lower()
    return any(re.search(kw, t) for kw in _GAMER_KEYWORDS)


def _buscar_url(url: str) -> list[dict]:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="pt-BR", user_agent=_UA, viewport={"width": 1280, "height": 1024})
        try:
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=40000)
            except PlaywrightTimeout:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_selector(".andes-card.poly-card, [class*='poly-card--grid']", timeout=10000)
            except PlaywrightTimeout:
                pass
            html = page.content()
            produtos = _extrair_produtos_json(html)
            if not produtos:
                raw = page.evaluate(_DOM_SCRIPT)
                produtos = _normalizar_dom(raw)
        finally:
            try:
                browser.close()
            except Exception:
                pass
    return produtos


def main_busca():
    todos = []
    for url in _URLS:
        print(f"Buscando: {url}")
        try:
            brutos = _buscar_url(url)
            print(f"  {len(brutos)} produtos brutos.")
            todos.extend(_filtrar_e_afiliar(brutos, "celulares", desconto_min=5, limite=100))
        except Exception as e:
            print(f"  Falhou: {e}")

    vistos = set()
    candidatos = []
    for p in todos:
        titulo = p.get("titulo", "")
        preco = p.get("preco")
        desconto = p.get("desconto_pct", 0)
        if not titulo or not preco:
            continue
        link_id = p["link"].split("?")[0]
        if link_id in vistos:
            continue
        if _parece_acessorio(titulo):
            continue
        if "motorola" in titulo.lower():
            continue
        if not _e_gamer(titulo):
            continue
        ram = _extrair_ram(titulo)
        if ram is None or ram < RAM_MIN:
            continue
        if preco > PRECO_MAX:
            continue
        if not p.get("preco_original") or desconto < DESCONTO_MIN:
            continue
        vistos.add(link_id)
        p["ram_detectada"] = ram
        candidatos.append(p)

    candidatos.sort(key=lambda p: (p.get("mais_vendido", False), p["desconto_pct"], p["preco"]), reverse=True)
    return candidatos


async def enviar(candidatos: list[dict]):
    print(f"\nEnviando {len(candidatos)} ofertas gamer (sem Motorola, 8GB+ RAM, ate R${PRECO_MAX:.0f})...")
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
    print(f"\n{len(candidatos)} candidatos gamer (sem Motorola, RAM>={RAM_MIN}GB, preco<=R${PRECO_MAX:.0f}, desconto>={DESCONTO_MIN}%):\n")
    for p in candidatos:
        print(f"- [{p['ram_detectada']}GB RAM] {p['titulo'][:70]} | R${p['preco']:.2f} (de R${p['preco_original']:.2f}, -{p['desconto_pct']}%)")

    with open("data/_candidatos_gamers.json", "w", encoding="utf-8") as f:
        json.dump(candidatos, f, ensure_ascii=False, indent=2)
    print("\nSalvo em data/_candidatos_gamers.json")

    if candidatos:
        asyncio.run(enviar(candidatos))
    else:
        print("Nenhum candidato gamer bateu os criterios — nada enviado.")
