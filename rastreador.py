# -*- coding: utf-8 -*-
"""
RASTREADOR AUTOMÁTICO DE OFERTAS
==================================
Busca produtos em promoção no Mercado Livre, gera links OFICIAIS meli.la
via portal de afiliados e publica no Telegram.

Regra de ouro: só publica com link oficial de afiliado gerado.
Se a geração falhar → produto enfileirado como pendente, NÃO publicado.

Como usar:
    python rastreador.py              → roda uma vez agora
    python rastreador.py --loop 60   → roda a cada 60 minutos continuamente
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
from core.validador import validar
from core.scheduler import e_bom_momento, resumo_horario
import core.database as db
from affiliates.registry import get_provider, health_report
from integrations.ml_browser import buscar_ofertas_browser_async
from integrations.telegram_bot import publicar

try:
    from core.ai_rewriter import reescrever_titulo, reescrever_descricao
    _AI_OK = True
except ImportError:
    _AI_OK = False
    def reescrever_titulo(p): return None  # noqa: E731
    def reescrever_descricao(p): return None  # noqa: E731

load_dotenv()

TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "")
CANAIS = {"geral": os.getenv("CANAL_GERAL", "")}

# ── Configuração ──────────────────────────────────────────────────────────────
CATEGORIAS_ATIVAS = ["celulares", "eletronicos", "informatica", "casa", "esportes", "moda"]
DESCONTO_MINIMO   = 20
SCORE_MINIMO      = 60
MAX_POR_EXECUCAO  = 1
PAUSA_ENTRE_POSTS = 10   # segundos

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def log(msg: str) -> None:
    print(msg)
    logging.info(msg)


# ── Deduplicação via banco de dados ──────────────────────────────────────────

def _id_produto(item: dict) -> str:
    """ID estável baseado na URL limpa do produto."""
    url = item.get("link", "")
    return url.split("?")[0].rstrip("/").split("/")[-1] or url[:60]


def _e_duplicata(item: dict) -> bool:
    url_base = item.get("link", "").split("?")[0].rstrip("/")
    return db.link_ja_existe(url_base)


# ── Processamento de cada categoria ──────────────────────────────────────────

async def processar_categoria(
    bot: Bot,
    nicho: str,
    publicados: list[int],
    exec_id: int,
    contadores: dict,
) -> None:
    if publicados[0] >= MAX_POR_EXECUCAO:
        return

    log(f"\n🔍 [{nicho}] buscando ofertas...")
    try:
        itens = await buscar_ofertas_browser_async(
            nicho, desconto_min=DESCONTO_MINIMO, limite=20,
        )
    except Exception as e:
        log(f"  ❌ Erro ao buscar [{nicho}]: {e}")
        db.registrar_erro("scraping", str(e))
        contadores["erros"] += 1
        return

    contadores["encontrados"] += len(itens)
    log(f"  {len(itens)} produto(s) com desconto ≥{DESCONTO_MINIMO}%")

    for item in itens:
        if publicados[0] >= MAX_POR_EXECUCAO:
            break

        titulo = (item.get("titulo") or "")
        titulo_curto = titulo[:55]

        if not titulo or not item.get("link"):
            continue

        produto_id = _id_produto(item)
        item["id"] = produto_id

        # ── 1. Duplicata ──────────────────────────────────────────────────────
        if _e_duplicata(item):
            log(f"  ↩️  Duplicata: {titulo_curto}")
            contadores["duplicatas"] += 1
            continue

        # ── 2. Validação anti-golpe ───────────────────────────────────────────
        aprovado, motivo = validar(item, reputacao={})
        if not aprovado:
            log(f"  ⚠️  Rejeitado [{motivo}]: {titulo_curto}")
            contadores["erros"] += 1
            continue

        # ── 3. Score ──────────────────────────────────────────────────────────
        score = calcular_score(item)
        item["score"] = score

        if score < SCORE_MINIMO:
            log(f"  📊 Score {score} < {SCORE_MINIMO}: {titulo_curto}")
            continue

        log(f"  📊 {titulo_curto} | {item.get('desconto_pct', 0):.0f}% OFF | score {score}")

        # ── 4. Gerar link de afiliado ─────────────────────────────────────────
        url_original = item.get("link", "").split("?")[0]
        provider = get_provider(url_original)

        if provider is None:
            log(f"  ❌ Nenhum provedor para {url_original[:60]}")
            db.registrar_erro("affiliate", f"sem provedor para {url_original}", produto_id)
            contadores["links_falharam"] += 1
            continue

        log(f"     🔗 Gerando link de afiliado ({provider.name})...")
        try:
            link_afiliado = await provider.generate_affiliate_link_async(url_original)
        except Exception as e:
            link_afiliado = None
            log(f"     ❌ Erro ao gerar link: {e}")
            db.registrar_erro("affiliate", str(e), produto_id)

        if not link_afiliado or not provider.validate_affiliate_link(link_afiliado):
            log(f"     ❌ Falha total ao gerar link — pulando {titulo_curto}")
            item["status"] = "pendente"
            item["adicionado_em"] = datetime.now().isoformat()
            db.inserir_produto(item)
            db.atualizar_afiliado(produto_id, provider.name, "", "erro")
            contadores["links_falharam"] += 1
            continue

        eh_melila = "meli.la/" in link_afiliado
        tipo_link = "meli.la oficial" if eh_melila else "link direto c/ afiliado"
        log(f"     ✅ {tipo_link}: {link_afiliado[:80]}")
        contadores["links_gerados"] += 1
        item["link"] = link_afiliado

        # ── 5. Reescrita com IA (opcional) ───────────────────────────────────
        titulo_ia = None
        if _AI_OK:
            try:
                titulo_ia = reescrever_titulo(item)
                if titulo_ia:
                    log(f"     🤖 Título IA: {titulo_ia[:55]}")
            except Exception:
                pass

        # ── 6. Publicar ───────────────────────────────────────────────────────
        sucesso = await publicar(bot, item, CANAIS, titulo_reescrito=titulo_ia)
        if sucesso:
            item["status"] = "enviado"
            item["adicionado_em"] = datetime.now().isoformat()
            db.inserir_produto(item)
            db.atualizar_afiliado(produto_id, provider.name, link_afiliado, "ok")
            db.marcar_enviado(produto_id)
            publicados[0] += 1
            contadores["publicados"] += 1
            log(f"  ✅ Publicado! ({publicados[0]}/{MAX_POR_EXECUCAO})")
            await asyncio.sleep(PAUSA_ENTRE_POSTS)
        else:
            db.registrar_erro("telegram", "falha ao publicar", produto_id)
            contadores["erros"] += 1


# ── Execução principal ────────────────────────────────────────────────────────

async def rodar_uma_vez() -> None:
    t_inicio = time.time()
    db.inicializar()

    if not TOKEN_TELEGRAM:
        print("❌ TOKEN_TELEGRAM não definido no .env")
        return

    exec_id = db.iniciar_execucao()
    contadores = {
        "encontrados": 0,
        "links_gerados": 0,
        "links_falharam": 0,
        "publicados": 0,
        "duplicatas": 0,
        "erros": 0,
    }

    log("\n" + "=" * 55)
    log(f"Rastreador iniciado — {resumo_horario()}")

    # Status dos provedores
    saude = health_report()
    for nome, ok in saude.items():
        status = "✅ sessão ativa (meli.la)" if ok else "🔗 link direto c/ afiliado"
        log(f"  {nome}: {status}")

    if not e_bom_momento():
        log("⏰ Fora do horário ideal, prosseguindo mesmo assim")

    publicados: list[int] = [0]

    async with Bot(token=TOKEN_TELEGRAM) as bot:
        for nicho in CATEGORIAS_ATIVAS:
            if publicados[0] >= MAX_POR_EXECUCAO:
                break
            await processar_categoria(bot, nicho, publicados, exec_id, contadores)

    db.finalizar_execucao(
        exec_id,
        produtos_encontrados=contadores["encontrados"],
        links_gerados=contadores["links_gerados"],
        links_falharam=contadores["links_falharam"],
        publicados=contadores["publicados"],
        duplicatas=contadores["duplicatas"],
        erros=contadores["erros"],
    )

    log(f"\n{'=' * 55}")
    log(
        f"Rodada concluída — {contadores['publicados']} publicado(s), "
        f"{contadores['links_gerados']} link(s) oficial(is), "
        f"{contadores['links_falharam']} falha(s) de link."
    )
    log(f"⏱️  Tempo total: {time.time() - t_inicio:.1f}s")

    # Monitoramento (alerta se muitos erros)
    try:
        if contadores["erros"] > 5:
            from core.monitor import verificar_e_alertar
            verificar_e_alertar(TOKEN_TELEGRAM, list(CANAIS.values())[0])
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Rastreador automático de ofertas ML")
    parser.add_argument(
        "--loop", type=int, metavar="MINUTOS",
        help="Rodar em loop a cada N minutos (ex: --loop 60)"
    )
    args = parser.parse_args()

    if args.loop:
        log(f"Modo contínuo: a cada {args.loop} minuto(s). Ctrl+C para parar.")
        while True:
            asyncio.run(rodar_uma_vez())
            log(f"\n⏳ Próxima rodada em {args.loop} minuto(s)...")
            time.sleep(args.loop * 60)
    else:
        asyncio.run(rodar_uma_vez())


if __name__ == "__main__":
    main()
