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
import re
import time
from datetime import datetime

from dotenv import load_dotenv
from telegram import Bot

from core.error_logger import setup_logging
setup_logging()
from core.scorer import score_inteligente
from core.validador import validar
from core.scheduler import e_bom_momento, resumo_horario
import core.database as db
from affiliates.registry import get_provider, health_report
from integrations.ml_browser import buscar_ofertas_browser_async
from integrations.telegram_bot import publicar, publicar_alerta_cupom
from integrations.social_poster import publicar_todas_redes, resumo_redes
from integrations.whatsapp_sender import wa_ativo
from core import pausa
from core.net import dns_ok
from integrations import n8n

try:
    from core.ai_content import gerar_conteudo
    _AI_OK = True
except ImportError:
    _AI_OK = False
    def gerar_conteudo(p): return {"titulo_telegram": None, "descricao_telegram": None, "mensagem_whatsapp": None, "ia_usada": False}  # noqa: E731

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

# Timeouts do cliente HTTP do Telegram: a definição mora em
# integrations/telegram_bot.py, porque os TRÊS processos que publicam (ML,
# Amazon e campanha de ferramentas) precisam dela. Aqui é só o atalho.
def criar_bot() -> Bot:
    """Bot do Telegram com timeouts próprios (ver `telegram_bot.criar_bot`)."""
    from integrations.telegram_bot import criar_bot as _criar  # noqa: PLC0415

    return _criar(TOKEN_TELEGRAM)


def log(msg: str) -> None:
    print(msg)
    logging.info(msg)


# ── Deduplicação via banco de dados ──────────────────────────────────────────

_RE_MLB_ID = re.compile(r"MLBU?-?\d+", re.IGNORECASE)


def _id_produto(item: dict) -> str:
    """ID estável: prioriza o ID oficial do anúncio (ml_id do scraper, ou
    MLB/MLBU extraído da própria URL) sobre o slug da URL — o slug muda
    de raspagem pra raspagem quando a URL carrega fragmento de tracking
    do carrossel, quebrando a deduplicação mesmo com o item inalterado."""
    ml_id = item.get("ml_id")
    if ml_id:
        return str(ml_id).upper()
    url = item.get("link", "")
    m = _RE_MLB_ID.search(url)
    if m:
        return m.group(0).replace("-", "").upper()
    return url.split("?")[0].split("#")[0].rstrip("/").split("/")[-1] or url[:60]


def _e_duplicata(item: dict) -> bool:
    # Usa o ID estável (slug da URL, já setado em item["id"] antes de chamar
    # isto) em vez do link — o link pode virar meli.la/XXXXX depois do
    # afiliado oficial, sem nenhuma relação textual com a URL original.
    produto_id = item.get("id") or _id_produto(item)
    return db.produto_id_existe(produto_id)


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

    postados_categoria = 0  # respeita MAX_POR_CATEGORIA — antes essa constante existia mas nunca era checada

    for item in itens:
        if publicados[0] >= MAX_POR_EXECUCAO:
            break
        if postados_categoria >= MAX_POR_CATEGORIA:
            break

        titulo = (item.get("titulo") or "")
        titulo_curto = titulo[:55]

        if not titulo or not item.get("link"):
            continue

        produto_id = _id_produto(item)
        item["id"] = produto_id

        try:
            # ── 0. Histórico de preço (registra mesmo se for duplicata) ───────────
            db.registrar_preco(produto_id, item.get("preco"))

            # ── 1. Duplicata ──────────────────────────────────────────────────────
            if _e_duplicata(item):
                log(f"  ↩️  Duplicata: {titulo_curto}")
                contadores["duplicatas"] += 1
                continue

            # ── 1b. Quarentena de publicação ──────────────────────────────────────
            # Produto que já falhou MAX_TENTATIVAS_PUBLICACAO vezes seguidas
            # fica de fora até a quarentena expirar. Sem isso, o mesmo item
            # voltava a cada rodada, falhava de novo e ainda consumia uma das
            # 4 vagas de publicação da rodada -- foi o que aconteceu com o
            # MLB68674214 (5 tentativas registradas entre 11:27 e 17:25 de
            # 2026-08-25, nenhuma oferta publicada no lugar dele).
            if db.em_quarentena(produto_id):
                log(f"  🚫 Em quarentena (falhas anteriores): {titulo_curto}")
                contadores["duplicatas"] += 1
                continue

            # ── 2. Validação anti-golpe ───────────────────────────────────────────
            aprovado, motivo = validar(item, reputacao={})
            if not aprovado:
                log(f"  ⚠️  Rejeitado [{motivo}]: {titulo_curto}")
                contadores["erros"] += 1
                continue

            # ── 3. Score ──────────────────────────────────────────────────────────
            score = score_inteligente(item)
            item["score"] = score

            if score < SCORE_MINIMO:
                log(f"  📊 Score {score} < {SCORE_MINIMO}: {titulo_curto}")
                continue

            log(f"  📊 {titulo_curto} | {item.get('desconto_pct', 0):.0f}% OFF | score {score}")

            # ── 4. Gerar link de afiliado ─────────────────────────────────────────
            url_original = item.get("link", "").split("?")[0].split("#")[0]
            provider = get_provider(url_original)

            if provider is None:
                log(f"  ❌ Nenhum provedor para {url_original[:60]}")
                db.registrar_erro("affiliate", f"sem provedor para {url_original}", produto_id)
                contadores["links_falharam"] += 1
                continue

            # Reivindicação atômica — fecha a corrida com campanha_ferramentas.py
            # (roda como processo separado e escaneia a MESMA categoria
            # "ferramentas"): sem isso, os dois podiam checar _e_duplicata()
            # como False antes de qualquer um gravar no banco, e publicar o
            # mesmo produto 2x no mesmo canal. Feita bem antes das duas
            # chamadas de rede lentas (geração de link + publicação) que
            # criam a janela de corrida de verdade.
            if not db.claim_produto(produto_id, titulo):
                log(f"  ↩️  Já reivindicado por outro processo: {titulo_curto}")
                contadores["duplicatas"] += 1
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
                # Libera a reivindicação (não persiste com status='pendente')
                # pra essa falha — normalmente transitória — não congelar o
                # produto por até 2 dias (até o limpar_antigos rodar); o erro
                # já fica registrado em erros_log via registrar_erro acima.
                db.liberar_claim(produto_id)
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
                db.atualizar_produto(item)
                db.atualizar_afiliado(produto_id, provider.name, link_afiliado, "ok")
                db.marcar_enviado(produto_id)
                db.limpar_falha_publicacao(produto_id)
                publicados[0] += 1
                postados_categoria += 1
                contadores["publicados"] += 1
                log(f"  ✅ Publicado! ({publicados[0]}/{MAX_POR_EXECUCAO})")

                # Evento para o n8n — alimenta os workflows de divulgação,
                # painel e relatório diário. Assíncrono: não atrasa a rodada.
                n8n.emitir("oferta_publicada", {
                    "produto_id": produto_id,
                    "titulo": item.get("titulo"),
                    "preco": item.get("preco"),
                    "preco_original": item.get("preco_original"),
                    "desconto_pct": item.get("desconto_pct"),
                    "categoria": item.get("categoria") or nicho,
                    "score": item.get("score"),
                    "foto": item.get("foto"),
                    "link": link_afiliado,
                    "fonte": "mercadolivre",
                })

                # Métricas — Telegram e Mercado Livre (best-effort, não pode quebrar o fluxo)
                try:
                    from core.metrics import inc, set_gauge
                    inc("posts_telegram_total")
                    inc("posts_ml_total")
                    set_gauge("ultimo_post_ts", time.time())
                except Exception:
                    pass

                # Blog — gera landing/index/sitemap em background (best-effort,
                # NUNCA pode derrubar a publicação já concluída no Telegram)
                try:
                    from core.blog_generator import gerar_tudo
                    gerar_tudo(item)
                except Exception as _e_blog:
                    from core.error_logger import log_erro
                    log_erro("blog_generator.falhou", _e_blog, {"produto_id": produto_id})

                # WhatsApp em segundo plano — ISOLADO do Telegram (best-effort).
                # WhatsApp não sai mais na hora junto com o Telegram -- entra
                # na fila e sai sozinho num intervalo aleatório de 30-45min
                # (ver whatsapp_queue_sender.py). Pedido do Daniel em
                # 2026-08-24: publicar nos dois lugares quase ao mesmo tempo,
                # sempre, é um padrão fácil de reconhecer como bot. Telegram
                # continua sem depender do WhatsApp -- enfileirar_whatsapp()
                # é só uma escrita local, nunca atrasa nem falha a rodada.
                if wa_ativo():
                    item_fila = dict(item)
                    if texto_wa:
                        item_fila["mensagem_override"] = texto_wa
                    db.enfileirar_whatsapp(item_fila)
                    log(f"     💚 WhatsApp: na fila ({db.tamanho_fila_whatsapp()} pendente(s))")

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
                falha = db.registrar_falha_publicacao(
                    produto_id, "falha ao publicar no Telegram", titulo,
                )
                if falha["quarentena"]:
                    # Esgotou as tentativas: sai de rotação até a quarentena
                    # expirar. A linha do produto FICA no banco (status
                    # 'quarentena') de propósito -- é ela que faz
                    # _e_duplicata() barrar o item na próxima raspagem, antes
                    # mesmo de gastar uma chamada de rede com ele.
                    item["status"] = "quarentena"
                    db.atualizar_produto(item)
                    log(f"  🚫 {falha['tentativas']}ª falha — produto em quarentena "
                        f"até {falha['quarentena_ate'][:16]}: {titulo_curto}")
                    n8n.emitir("produto_quarentena", falha)
                else:
                    # Ainda dentro do limite: libera a reivindicação pra
                    # permitir nova tentativa na próxima rodada em vez de
                    # travar o produto com status='processing'.
                    db.liberar_claim(produto_id)
                    log(f"  ⚠️  Falha {falha['tentativas']}/{falha['max_tentativas']} "
                        f"ao publicar: {titulo_curto}")
                contadores["erros"] += 1
        except Exception as e_item:
            # Um item malformado (campo inesperado, exceção não prevista em
            # validar/score/publicar) não pode derrubar a categoria inteira —
            # loga e segue para o próximo item.
            log(f"  ⚠️  Erro inesperado processando '{titulo_curto}': {e_item}")
            db.registrar_erro("item_falhou", str(e_item), produto_id)
            # Rede de segurança: se a reivindicação foi feita mas algo
            # explodiu antes de chegar num estado terminal (sucesso ou uma
            # das duas falhas tratadas acima), libera pra não travar o
            # produto pra sempre. Vira no-op se já foi resolvido (o guard
            # em liberar_claim só apaga linhas ainda com status='processing').
            db.liberar_claim(produto_id)
            contadores["erros"] += 1
            continue


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

    # Pausa operacional (bandeira em data/pausado.flag, criável pelo n8n via
    # POST /n8n/comando). Sai antes de abrir execução no banco: uma rodada
    # pausada não deve aparecer como execução vazia no histórico nem contar
    # como "sistema ocupado" para o desligamento noturno.
    if pausa.pausado():
        info_pausa = pausa.info()
        log(f"⏸️  Publicação pausada desde {info_pausa.get('pausado_em', '?')} "
            f"({info_pausa.get('motivo', '')}) — nada a fazer nesta rodada.")
        return

    # Papel desta instancia (core/papel.py). No PC nao muda nada — sem a
    # variavel PAPEL o papel e "local" e a resposta e sempre "pode". Num
    # servidor de nuvem e o que impede de publicar em cima do PC ligado:
    # os bancos de deduplicacao sao separados, entao os dois publicando ao
    # mesmo tempo mandam a MESMA oferta duas vezes para o grupo.
    from core import papel as _papel  # noqa: PLC0415

    _bloqueado, _motivo_papel = _papel.bloqueado()
    if _bloqueado:
        log(f"\u23f8\ufe0f  Rodada ML nao publica: {_motivo_papel}")
        return

    # Pré-checagem de DNS. Sem rede, cada passo seguinte gastaria dezenas de
    # segundos em timeout até a rodada morrer com um "Timed out" genérico
    # (registro real de 2026-08-25 23:20). Aqui isso vira uma saída em ~3s,
    # com causa nomeada no log e no relatório de problemas.
    if not dns_ok():
        log("🌐 Sem resolução de DNS — pulando a rodada (rede fora do ar).")
        db.registrar_erro("rede", "DNS indisponível — rodada pulada")
        n8n.emitir("rodada_pulada", {"motivo": "dns_indisponivel"})
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

    # try/finally: sem isso, uma exceção não capturada em algum ponto do
    # loop (ex: dentro do "async with Bot" em si, fora do try/except já
    # existente em processar_categoria) deixava exec_id aberto pra sempre
    # — confirmado ao vivo em 2026-08-03 (várias linhas "Rodada falhou
    # inesperadamente: Timed out" com a execução correspondente nunca
    # finalizada). execucao_em_andamento() conta qualquer execução aberta
    # há menos de 20min como "sistema ocupado", então uma rodada travada
    # podia atrasar o desligamento noturno por até 20min à toa.
    try:
        async with criar_bot() as bot:
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
    finally:
        db.finalizar_execucao(
            exec_id,
            produtos_encontrados=contadores["encontrados"],
            links_gerados=contadores["links_gerados"],
            links_falharam=contadores["links_falharam"],
            publicados=contadores["publicados"],
            duplicatas=contadores["duplicatas"],
            erros=contadores["erros"],
        )

    # Métrica — uma rodada completa (incondicional, best-effort)
    try:
        from core.metrics import inc
        inc("rodadas_completadas")
    except Exception:
        pass

    log(f"\n{'=' * 55}")
    log(
        f"Rodada concluída — {contadores['publicados']} publicado(s), "
        f"{contadores['links_gerados']} link(s) oficial(is), "
        f"{contadores['links_falharam']} falha(s) de link."
    )
    log(f"⏱️  Tempo total: {time.time() - t_inicio:.1f}s")

    # Resumo da rodada para o n8n (painel, relatório diário e watchdog).
    n8n.emitir("rodada_concluida", {
        "duracao_s": round(time.time() - t_inicio, 1),
        "fonte": "mercadolivre",
        **contadores,
    })

    # Publica as paginas SEO novas (docs/ofertas/) geradas nesta rodada —
    # best-effort, nunca pode derrubar o rastreador (ver core/site_publisher.py)
    if contadores["publicados"]:
        try:
            from core.site_publisher import publicar_site
            publicar_site(origem="rastreador-ml")
        except Exception:
            pass

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
            try:
                asyncio.run(rodar_uma_vez())
            except Exception as e:
                log(f"⚠️  Rodada falhou inesperadamente: {e}")
                db.registrar_erro("rodada_falhou", str(e))
                n8n.emitir("rodada_falhou", {
                    "erro": f"{type(e).__name__}: {e}"[:300], "fonte": "mercadolivre",
                })
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
