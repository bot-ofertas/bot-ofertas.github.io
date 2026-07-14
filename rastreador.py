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
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import logging
import os
import random
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
from integrations.telegram_bot import publicar, publicar_alerta_cupom
from integrations.social_poster import publicar_todas_redes, resumo_redes
from integrations.whatsapp_sender import enviar_para_grupo, wa_ativo

try:
    from core.ai_content import gerar_conteudo, ia_ativa
    _AI_OK = True
except ImportError:
    _AI_OK = False
    def gerar_conteudo(p): return {"titulo_telegram": None, "descricao_telegram": None, "mensagem_whatsapp": None, "ia_usada": False}  # noqa: E731
    def ia_ativa(): return False  # noqa: E731

load_dotenv()

TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "")
CANAIS = {"geral": os.getenv("CANAL_GERAL", "")}

# ── Configuração ──────────────────────────────────────────────────────────────
CATEGORIAS_ATIVAS = [
    # Celulares
    "celulares",
    # Informática — subcategorias separadas
    "notebooks", "tablets", "informatica", "monitores",
    "armazenamento", "impressoras", "redes",
    # Eletrônicos
    "eletronicos", "tvs", "audio", "cameras",
    # Casa
    "casa", "eletrodomesticos", "moveis",
    # Moda e Beleza
    "moda", "beleza", "saude",
    # Esportes e Lazer
    "esportes", "games", "brinquedos",
    # Família
    "bebes", "livros",
    # Veículos
    "automotivo", "ferramentas",
    # Pet
    "pet",
]
DESCONTO_MINIMO   = 20
SCORE_MINIMO      = 60
MAX_POR_EXECUCAO  = 4   # 4 posts por execução × 18 runs/dia = ~72 posts/dia
MAX_POR_CATEGORIA = 1   # nunca posta a mesma categoria 2x no mesmo run
PAUSA_ENTRE_POSTS = 6   # segundos entre posts

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

        # ── 0. Histórico de preço (registra mesmo se for duplicata) ───────────
        db.registrar_preco(produto_id, item.get("preco"))

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

        # Sinal de confiança: menor preço no período (se houver histórico)
        try:
            hist = db.historico_preco(produto_id, dias=30)
            item["hist_preco"] = hist
            if hist.get("e_menor_periodo"):
                log(f"     📉 Menor preço em {hist['dias']} dias!")
        except Exception:
            pass

        # ── 5. Geração de conteúdo com IA ────────────────────────────────────
        conteudo_ia = {"titulo_telegram": None, "descricao_telegram": None,
                       "mensagem_whatsapp": None, "ia_usada": False}
        if _AI_OK:
            try:
                conteudo_ia = gerar_conteudo(item)
                if conteudo_ia.get("ia_usada"):
                    log(f"     🤖 IA: {conteudo_ia['titulo_telegram'][:55]}")
                else:
                    log("     ℹ️  IA indisponível — usando conteúdo padrão")
            except Exception as _e_ia:
                log(f"     ⚠️  IA: {_e_ia}")

        titulo_ia = conteudo_ia.get("titulo_telegram")
        descricao_ia = conteudo_ia.get("descricao_telegram")
        texto_wa = conteudo_ia.get("mensagem_whatsapp")

        # ── 6. Publicar Telegram + WhatsApp simultaneamente ───────────────────
        tem_cupom = bool(item.get("cupom"))
        if tem_cupom:
            log(f"     🏷️  Cupom detectado: {item['cupom']} — enviando ALERTA CUPOM")
            sucesso = await publicar_alerta_cupom(bot, item, CANAIS)
        else:
            sucesso = await publicar(bot, item, CANAIS,
                                     titulo_reescrito=titulo_ia,
                                     descricao_reescrita=descricao_ia)
        if sucesso:
            item["status"] = "enviado"
            item["adicionado_em"] = datetime.now().isoformat()
            db.inserir_produto(item)
            db.atualizar_afiliado(produto_id, provider.name, link_afiliado, "ok")
            db.marcar_enviado(produto_id)
            publicados[0] += 1
            contadores["publicados"] += 1
            log(f"  ✅ Publicado! ({publicados[0]}/{MAX_POR_EXECUCAO})")

            # WhatsApp em segundo plano — ISOLADO do Telegram (best-effort).
            # Se WhatsApp travar/falhar por qualquer motivo, o Telegram continua.
            # Timeout de 90s por envio evita bloquear a rodada inteira.
            if wa_ativo():
                try:
                    wa_ok = await asyncio.wait_for(
                        enviar_para_grupo(item, mensagem_override=texto_wa),
                        timeout=90.0,
                    )
                    log(f"     💚 WhatsApp: {'enviado' if wa_ok else 'falhou (sessão?)'}")
                except asyncio.TimeoutError as _e:
                    from core.error_logger import log_erro
                    log_erro("wa.timeout_90s", _e,
                             {"produto_id": produto_id, "titulo": titulo_curto})
                    log("     ⏱️  WhatsApp: timeout 90s (Chrome ocupado ou caído)")
                except Exception as _e_wa:
                    from core.error_logger import log_erro
                    log_erro("wa.envio_falha", _e_wa,
                             {"produto_id": produto_id, "titulo": titulo_curto})
                    log(f"     ⚠️  WhatsApp: {_e_wa}")

            # Demais redes (Instagram, Twitter…)
            try:
                redes = await publicar_todas_redes(item)
                if redes:
                    log(f"     🌐 Redes: {resumo_redes(redes)}")
            except Exception as _e_social:
                log(f"     ⚠️  Social: {_e_social}")
            await asyncio.sleep(PAUSA_ENTRE_POSTS)
        else:
            db.registrar_erro("telegram", "falha ao publicar", produto_id)
            contadores["erros"] += 1


# ── Execução principal ────────────────────────────────────────────────────────

async def rodar_uma_vez() -> None:
    t_inicio = time.time()
    db.inicializar()
    removidos = db.limpar_antigos(dias=2)
    if removidos:
        log(f"🧹 Limpeza automática: {removidos} produto(s) antigos removidos do banco")

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

    # Rotaciona e distribui — garante variedade de categorias por run
    ordem = CATEGORIAS_ATIVAS[:]
    random.shuffle(ordem)
    log(f"  Ordem desta rodada: {' → '.join(ordem)}")

    categorias_postadas: set[str] = set()

    async with Bot(token=TOKEN_TELEGRAM) as bot:
        for nicho in ordem:
            if publicados[0] >= MAX_POR_EXECUCAO:
                break
            # Nunca posta a mesma categoria duas vezes no mesmo run
            if nicho in categorias_postadas:
                continue
            antes = publicados[0]
            await processar_categoria(bot, nicho, publicados, exec_id, contadores)
            if publicados[0] > antes:
                categorias_postadas.add(nicho)

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

    # Fecha o navegador do WhatsApp (libera o event loop desta rodada)
    try:
        from integrations.whatsapp_playwright import fechar_whatsapp
        await fechar_whatsapp()
    except Exception:
        pass

    # Monitoramento (alerta se muitos erros)
    try:
        if contadores["erros"] > 5:
            from core.monitor import verificar_e_alertar
            verificar_e_alertar(TOKEN_TELEGRAM, list(CANAIS.values())[0])
    except Exception:
        pass


def _ja_existe_outra_instancia() -> bool:
    """True se já houver outro rastreador.py em modo --loop rodando.

    Evita instâncias duplicadas que causam posts repetidos e conflito no
    pyautogui (vários processos disputando o WhatsApp Web ao mesmo tempo).
    """
    try:
        import psutil  # noqa: PLC0415
        meu_pid = os.getpid()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.info["pid"] == meu_pid:
                    continue
                cmd = " ".join(p.info.get("cmdline") or [])
                nome = (p.info.get("name") or "").lower()
                if "rastreador.py" in cmd and "--loop" in cmd and "python" in nome:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Rastreador automático de ofertas ML")
    parser.add_argument(
        "--loop", type=int, metavar="MINUTOS",
        help="Rodar em loop a cada N minutos fixos (ex: --loop 60)"
    )
    parser.add_argument(
        "--loop-min", type=int, default=30, metavar="MIN",
        help="Intervalo mínimo (min) para modo aleatório. Padrão: 30"
    )
    parser.add_argument(
        "--loop-max", type=int, default=45, metavar="MIN",
        help="Intervalo máximo (min) para modo aleatório. Padrão: 45"
    )
    parser.add_argument(
        "--random", action="store_true",
        help="Usa intervalo aleatório entre --loop-min e --loop-max (padrão: 30-45 min)"
    )
    args = parser.parse_args()

    if args.loop or args.random:
        if _ja_existe_outra_instancia():
            log("⛔ Outro rastreador --loop já está rodando. Encerrando para evitar duplicatas.")
            return
        if args.random:
            log(f"Modo contínuo ALEATÓRIO: {args.loop_min}-{args.loop_max} min. Ctrl+C para parar.")
        else:
            log(f"Modo contínuo: a cada {args.loop} minuto(s). Ctrl+C para parar.")
        while True:
            asyncio.run(rodar_uma_vez())
            if args.random:
                proximo = random.randint(args.loop_min, args.loop_max)
            else:
                proximo = args.loop
            log(f"\n⏳ Próxima rodada em {proximo} minuto(s)...")
            time.sleep(proximo * 60)
    else:
        asyncio.run(rodar_uma_vez())


if __name__ == "__main__":
    main()
