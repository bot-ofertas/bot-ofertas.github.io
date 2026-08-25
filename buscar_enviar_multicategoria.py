# -*- coding: utf-8 -*-
"""Script de uso único: varre peças de computador, ferramentas (elétricas e
manuais) e eletrodomésticos (priorizando mais vendidos) por descontos reais,
e envia o maior lote confiável possível para Telegram + WhatsApp.
Pedido pelo Daniel em 2026-08-21."""
import asyncio
import json
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

DESCONTO_MIN = 20.0
DESCONTO_MAX_SUSPEITO = 65.0  # acima disso, tratamos como provavel "de" inflado
POR_CATEGORIA = 4

GRUPOS = {
    "pecas_pc":       ["informatica", "armazenamento", "redes", "monitores"],
    "ferramentas":    ["ferramentas"],
    "eletrodomesticos": ["eletrodomesticos"],
}

# A pagina de ofertas do ML mistura itens fora do tema (relogio, album de
# figurinha, peca avulsa de reposicao) mesmo dentro da categoria certa --
# confirmado ao vivo em 2026-08-21 (informatica trouxe smartwatch e album
# de figurinha da Copa; eletrodomesticos trouxe tampa de mica avulsa e
# controlador de esteira de forno industrial). Allowlist por palavra-chave
# evita mandar isso pro grupo como se fosse peca de PC/eletrodomestico.
_PALAVRAS_PECAS_PC = (
    "placa de video", "placa-mae", "placa mae", "processador", "ryzen",
    "intel core", "memoria ram", "ram ddr", "ssd", "hd externo",
    "hd interno", "hd sata", "fonte atx", "fonte gamer", "gabinete gamer",
    "water cooler", "cooler", "placa de som", "placa de rede", "roteador",
    "switch de rede", "repetidor", "teclado mecanico", "teclado gamer",
    "mouse gamer", "nobreak", "monitor gamer", "monitor led", "monitor full hd",
    "monitor 4k", "webcam", "hub usb", "adaptador usb", "pendrive", "cartao de memoria",
)
_PALAVRAS_ELETRO = (
    "fogao", "geladeira", "refrigerador", "micro-ondas", "microondas",
    "lavadora", "maquina de lavar", "secadora de roupa", "freezer",
    "air fryer", "fritadeira", "liquidificador", "batedeira", "aspirador",
    "ventilador", "climatizador", "ar condicionado", "cafeteira",
    "forno eletrico", "depurador", "exaustor", "purificador de agua",
    "adega climatizada", "espremedor",
)
_PALAVRAS_EXCLUIR_SEMPRE = (
    "relogio", "smartwatch", "figurinha", "album", "copa do mundo",
    "capa", "capinha", "pelicula", "case ", "celular", "smartphone",
)
# "processador 6nm" aparece em specs de CELULAR (chip do aparelho, nao
# CPU de PC) -- achado ao vivo em 2026-08-21 (Galaxy A07 passou no filtro
# por causa disso). Peca de reposicao tambem cola no nome do eletro
# inteiro ("tampa de mica" de microondas) -- exclui os dois explicitamente
# em vez de confiar so no preco.
_PALAVRAS_EXCLUIR_ELETRO = ("tampa de mica", "guia de onda", "correia", "resistencia")
PRECO_MIN_ELETRO = 150.0


def _tema_correto(titulo: str, grupo: str) -> bool:
    t = titulo.lower()
    if any(k in t for k in _PALAVRAS_EXCLUIR_SEMPRE):
        return False
    if grupo == "pecas_pc":
        return any(k in t for k in _PALAVRAS_PECAS_PC)
    if grupo == "eletrodomesticos":
        if any(k in t for k in _PALAVRAS_EXCLUIR_ELETRO):
            return False
        return any(k in t for k in _PALAVRAS_ELETRO)
    return True  # ferramentas: categoria dedicada ja veio limpa, sem allowlist


def _plausivel(p: dict) -> bool:
    preco = p.get("preco")
    orig = p.get("preco_original")
    desc = p.get("desconto_pct", 0)
    if not preco or not orig or orig <= preco:
        return False
    if desc < DESCONTO_MIN or desc > DESCONTO_MAX_SUSPEITO:
        return False
    return True


def buscar_grupo(nome_grupo: str, categorias: list[str]) -> list[dict]:
    vistos = set()
    candidatos = []
    for cat in categorias:
        print(f"  Categoria '{cat}'...")
        try:
            produtos = buscar_ofertas_browser(cat, desconto_min=5, limite=100)
        except Exception as e:
            print(f"    Falhou: {e}")
            continue
        print(f"    {len(produtos)} produtos brutos.")
        for p in produtos:
            if not _plausivel(p):
                continue
            if not _tema_correto(p.get("titulo", ""), nome_grupo):
                continue
            if nome_grupo == "eletrodomesticos" and (p.get("preco") or 0) < PRECO_MIN_ELETRO:
                continue
            link_id = p["link"].split("?")[0]
            if link_id in vistos:
                continue
            vistos.add(link_id)
            candidatos.append(p)

    # Prioriza selo "mais vendido" do proprio ML, depois maior desconto
    candidatos.sort(key=lambda p: (p.get("mais_vendido", False), p["desconto_pct"]), reverse=True)
    return candidatos[:POR_CATEGORIA]


def main_busca() -> dict[str, list[dict]]:
    resultado = {}
    for nome_grupo, categorias in GRUPOS.items():
        print(f"\n=== {nome_grupo} ===")
        resultado[nome_grupo] = buscar_grupo(nome_grupo, categorias)
    return resultado


async def enviar(itens: list[dict]):
    print(f"\nEnviando {len(itens)} ofertas no total...")
    async with Bot(token=TOKEN_TELEGRAM) as bot:
        for i, p in enumerate(itens, 1):
            print(f"[{i}/{len(itens)}] {p['titulo'][:60]} | R${p['preco']:.2f} (-{p['desconto_pct']}%)")
            ok_tg = await publicar(bot, p, CANAIS)
            print(f"   Telegram: {'OK' if ok_tg else 'FALHOU'}")
            if wa_ativo():
                try:
                    ok_wa = await asyncio.wait_for(enviar_para_grupo(p), timeout=90.0)
                    print(f"   WhatsApp: {'OK' if ok_wa else 'falhou'}")
                except asyncio.TimeoutError:
                    print("   WhatsApp: timeout (>90s)")
            else:
                print("   WhatsApp: pausado (wa_ativo=False)")
            if i < len(itens):
                time.sleep(8)
    print("\nConcluido.")


if __name__ == "__main__":
    grupos = main_busca()

    todos = []
    for nome, itens in grupos.items():
        print(f"\n--- {nome}: {len(itens)} selecionados ---")
        for p in itens:
            print(f"  {p['titulo'][:70]} | R${p['preco']:.2f} (de R${p['preco_original']:.2f}, -{p['desconto_pct']}%) | selo={p.get('selo_ml','')}")
        todos.extend(itens)

    with open("data/_candidatos_multicategoria.json", "w", encoding="utf-8") as f:
        json.dump(grupos, f, ensure_ascii=False, indent=2)
    print(f"\nTotal geral: {len(todos)} ofertas. Salvo em data/_candidatos_multicategoria.json")

    if todos:
        asyncio.run(enviar(todos))
    else:
        print("Nada passou no filtro de desconto real — nada enviado.")
