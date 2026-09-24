# -*- coding: utf-8 -*-
"""
RASTREADOR MAGAZINE LUIZA
=========================
Terceiro publicador de marketplace, ao lado do Mercado Livre e da Amazon.

POR QUE ESTE ARQUIVO EXISTE: a regra combinada e UMA oferta de CADA loja
por rodada — ML + Amazon + Magalu = 3. So existiam dois rastreadores, e
`affiliates/magalu.py` era um stub que devolvia `None`. Entao nunca havia
como sair uma terceira oferta: o numero que faltava nao era de
configuracao, era um marketplace inteiro sem implementacao.

Pre-requisito:
    MAGALU_VITRINE=magazineSUAVITRINE   ← no .env (painel Parceiro Magalu)

Sem a vitrine este rastreador sai em silencio e sem custo de rede — nao
adianta raspar oferta que a Regra 7 vai descartar por falta de link de
afiliado valido.

Como usar:
    python rastreador_magalu.py            → roda uma vez
    python rastreador_magalu.py --loop 60  → a cada hora
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

from core.error_logger import setup_logging
setup_logging()
import core.database as db
from core.scorer import score_inteligente
from core.validador import validar
from affiliates.registry import get_provider
from integrations.magalu_scraper import buscar_ofertas_magalu_async
from integrations.telegram_bot import publicar
from integrations.social_poster import publicar_todas_redes, resumo_redes
from integrations.whatsapp_sender import fila_tera_quem_envie, wa_ativo

try:
    from core.ai_content import gerar_conteudo
except ImportError:
    def gerar_conteudo(p):  # noqa: E731
        return {"titulo_telegram": None, "descricao_telegram": None,
                "mensagem_whatsapp": None, "ia_usada": False}

load_dotenv()

TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "")
CANAIS = {"geral": os.getenv("CANAL_GERAL", "")}

# Mesma regra do ML e da Amazon: uma oferta por loja por rodada.
MAX_POR_EXECUCAO = max(1, int(os.getenv("MAX_POR_RODADA_MAGALU") or "1"))
DESCONTO_MINIMO = 20
SCORE_MINIMO = 60
PAUSA_ENTRE_POSTS = 8

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def log(msg: str) -> None:
    print(msg)
    logging.info(msg)


def _id_magalu(produto: dict) -> str:
    """ID estavel = codigo oficial do anuncio (`/p/<codigo>/`).

    Regra 11: usar o ID do anuncio em vez de derivar do slug. O mesmo
    produto aparece com slug diferente em vitrines diferentes, e derivar
    da URL faria o mesmo item ser republicado como se fosse novo.
    """
    codigo = produto.get("codigo")
    if codigo:
        return f"MLU{codigo}"
    from integrations.magalu_scraper import _E_PRODUTO  # noqa: PLC0415
    link = (produto.get("link") or "").split("?")[0].split("#")[0]
    achado = _E_PRODUTO.search(link)
    if achado:
        return "MLU" + achado.group(0).strip("/").split("/")[-1]
    return link[-20:]


async def rodar_uma_vez() -> None:
    from affiliates.magalu import magalu_ativo, vitrine  # noqa: PLC0415

    if not magalu_ativo():
        log("❌ MAGALU_VITRINE não configurada (ou em formato inválido).")
        log("   Adicione ao .env: MAGALU_VITRINE=magazineSUAVITRINE")
        log("   O nome sai do painel do Parceiro Magalu (parceiro.magalu.com).")
        return

    if not TOKEN_TELEGRAM:
        log("❌ TOKEN_TELEGRAM não definido.")
        return

    # Mesmas pré-checagens dos outros dois rastreadores. Elas moram aqui
    # (e não num helper compartilhado) porque cada uma pode divergir por
    # loja; o que NÃO pode é faltar uma — foi assim que a Amazon rodou
    # meses sem marcar "ocupado" e levou um desligamento no meio de um envio.
    from core import pausa  # noqa: PLC0415
    from core import papel as _papel  # noqa: PLC0415
    from core.net import dns_ok  # noqa: PLC0415
    from integrations import n8n  # noqa: PLC0415

    if pausa.pausado():
        log(f"⏸️  Publicação pausada ({pausa.info().get('motivo', '')}) — Magalu não roda.")
        return

    _bloqueado, _motivo_papel = _papel.bloqueado()
    if _bloqueado:
        log(f"⏸️  Rodada Magalu não publica: {_motivo_papel}")
        return

    if not dns_ok("www.magazineluiza.com.br"):
        log("🌐 Sem resolução de DNS — pulando a rodada Magalu.")
        db.registrar_erro("rede", "DNS indisponível — rodada Magalu pulada")
        n8n.emitir("rodada_pulada", {"motivo": "dns_indisponivel", "fonte": "magalu"})
        return

    db.inicializar()

    log("\n" + "=" * 55)
    log(f"Rastreador Magazine Luiza iniciado — vitrine {vitrine()}")

    exec_id = db.iniciar_execucao()
    publicados = 0
    produtos: list = []
    funil = None
    try:
        try:
            produtos = await buscar_ofertas_magalu_async(
                desconto_min=DESCONTO_MINIMO, limite=20,
            )
        except Exception as e:
            from core.error_logger import log_erro  # noqa: PLC0415
            log_erro("magalu.busca_falhou", e, {})
            log(f"  ⚠️  Busca Magalu falhou nesta rodada: {e}")
            produtos = []

        log(f"  {len(produtos)} produto(s) encontrado(s) no Magazine Luiza")

        from core.funil import Funil  # noqa: PLC0415
        funil = Funil("magalu", meta=MAX_POR_EXECUCAO)
        funil.encontradas(len(produtos))

        from integrations.telegram_bot import criar_bot  # noqa: PLC0415
        async with criar_bot(TOKEN_TELEGRAM) as bot:
            for indice, item in enumerate(produtos):
                if publicados >= MAX_POR_EXECUCAO:
                    funil.marcar("nao_avaliadas", len(produtos) - indice)
                    break

                titulo = (item.get("titulo") or "").strip()
                if not titulo or not item.get("link"):
                    funil.marcar("incompletas")
                    continue

                produto_id = _id_magalu(item)
                item["id"] = produto_id
                titulo_curto = titulo[:50]

                try:
                    db.registrar_preco(produto_id, item.get("preco"))

                    # Deduplicação: banco local + registro compartilhado do
                    # site (os três publicadores têm bancos separados e não
                    # enxergam um ao outro — Regra 16).
                    _ja = db.produto_id_existe(produto_id)
                    if not _ja:
                        try:
                            from core.publicados_site import ja_publicado  # noqa: PLC0415
                            _ja = ja_publicado(produto_id)
                        except Exception:
                            _ja = False
                    if _ja:
                        log(f"  ↩️  Duplicata: {titulo_curto}")
                        funil.marcar("duplicadas")
                        continue

                    if db.em_quarentena(produto_id):
                        log(f"  🚫 Em quarentena: {titulo_curto}")
                        funil.marcar("quarentena")
                        continue

                    aprovado, motivo = validar(item, reputacao={})
                    if not aprovado:
                        log(f"  ⚠️  Rejeitado [{motivo}]: {titulo_curto}")
                        funil.marcar("rejeitadas")
                        continue

                    score = score_inteligente(item)
                    item["score"] = score
                    if score < SCORE_MINIMO:
                        log(f"  📊 Score {score} < {SCORE_MINIMO}: {titulo_curto}")
                        funil.marcar("score_baixo")
                        continue

                    log(f"  📊 {titulo_curto} | {item.get('desconto_pct', 0):.0f}% OFF | score {score}")

                    # Reivindicação atômica antes das chamadas lentas —
                    # mesma corrida que o rastreador ML fecha.
                    if not db.claim_produto(produto_id, titulo):
                        log(f"  ↩️  Já reivindicado por outro processo: {titulo_curto}")
                        funil.marcar("reivindicadas")
                        continue

                    url_limpa = item["link"].split("?")[0].split("#")[0]
                    provedor = get_provider(url_limpa)
                    link_afiliado = provedor.generate_affiliate_link(url_limpa) if provedor else None

                    # Regra 7: sem link de afiliado VÁLIDO não publica. A
                    # validação é por parse do caminho, não por substring
                    # (ver affiliates/magalu.py).
                    if not link_afiliado or not provedor.validate_affiliate_link(link_afiliado):
                        log(f"  ❌ Sem link de afiliado válido — pulando {titulo_curto}")
                        db.registrar_erro("affiliate", f"magalu sem link para {url_limpa}", produto_id)
                        db.liberar_claim(produto_id)
                        funil.marcar("sem_afiliado")
                        continue

                    item["link"] = link_afiliado
                    log(f"     ✅ Vitrine: {link_afiliado[:80]}")

                    conteudo_ia = {}
                    try:
                        conteudo_ia = gerar_conteudo(item)
                        if conteudo_ia.get("ia_usada"):
                            log(f"     🤖 IA: {conteudo_ia.get('titulo_telegram', '')[:50]}")
                    except Exception as _e_ia:
                        log(f"     ⚠️  IA: {_e_ia}")

                    sucesso = await publicar(
                        bot, item, CANAIS,
                        titulo_reescrito=conteudo_ia.get("titulo_telegram"),
                        descricao_reescrita=conteudo_ia.get("descricao_telegram"),
                    )

                    if sucesso:
                        item["status"] = "enviado"
                        item["adicionado_em"] = datetime.now().isoformat()
                        item["affiliate_link"] = link_afiliado
                        db.inserir_produto(item)
                        db.atualizar_afiliado(produto_id, "magalu", link_afiliado, "ok")
                        db.marcar_enviado(produto_id)
                        db.limpar_falha_publicacao(produto_id)
                        publicados += 1
                        funil.marcar("publicadas")
                        log(f"  📤 Publicado! ({publicados}/{MAX_POR_EXECUCAO})")

                        try:
                            n8n.emitir("oferta_publicada", {
                                "produto_id": produto_id,
                                "titulo": item.get("titulo"),
                                "preco": item.get("preco"),
                                "preco_original": item.get("preco_original"),
                                "desconto_pct": item.get("desconto_pct"),
                                "categoria": item.get("categoria", "magalu"),
                                "score": item.get("score"),
                                "foto": item.get("foto"),
                                "link": link_afiliado,
                                "fonte": "magalu",
                            })
                        except Exception:
                            pass

                        try:
                            from core.metrics import inc, set_gauge  # noqa: PLC0415
                            inc("posts_telegram_total")
                            inc("posts_magalu_total")
                            set_gauge("ultimo_post_ts", time.time())
                        except Exception:
                            pass

                        try:
                            from core.blog_generator import gerar_tudo  # noqa: PLC0415
                            gerar_tudo(item)
                        except Exception as _e_blog:
                            from core.error_logger import log_erro  # noqa: PLC0415
                            log_erro("blog_generator.falhou", _e_blog, {"produto_id": produto_id})

                        # WhatsApp pela fila (intervalo randômico 30-45min,
                        # Regra 5) — nunca sai junto com o Telegram.
                        if wa_ativo() and fila_tera_quem_envie():
                            item_fila = dict(item)
                            msg_wa = conteudo_ia.get("mensagem_whatsapp")
                            if msg_wa:
                                item_fila["mensagem_override"] = msg_wa
                            db.enfileirar_whatsapp(item_fila)
                            log(f"     💚 WhatsApp: na fila ({db.tamanho_fila_whatsapp()} pendente(s))")
                        elif wa_ativo():
                            log("     💚 WhatsApp: sem quem envie neste ambiente — não enfileirado")

                        try:
                            redes = await publicar_todas_redes(item)
                            if redes:
                                log(f"     🌐 Redes: {resumo_redes(redes)}")
                        except Exception as _e:
                            log(f"     ⚠️  Social: {_e}")

                        await asyncio.sleep(PAUSA_ENTRE_POSTS)
                    else:
                        # Regra 12: falha conta tentativa e vai para
                        # quarentena — nunca volta para a rotação sem limite.
                        funil.marcar("falharam")
                        db.registrar_erro("telegram", "falha ao publicar", produto_id)
                        falha = db.registrar_falha_publicacao(
                            produto_id, "falha ao publicar no Telegram (Magalu)", titulo,
                        )
                        if falha["quarentena"]:
                            log(f"  🚫 {falha['tentativas']}ª falha — quarentena até "
                                f"{falha['quarentena_ate'][:16]}: {titulo_curto}")
                            try:
                                n8n.emitir("produto_quarentena", falha)
                            except Exception:
                                pass
                        else:
                            db.liberar_claim(produto_id)
                            log(f"  ⚠️  Falha {falha['tentativas']}/{falha['max_tentativas']} "
                                f"ao publicar: {titulo_curto}")
                except Exception as e_item:
                    from core.error_logger import log_erro  # noqa: PLC0415
                    log_erro("magalu.item_falhou", e_item, {"produto_id": produto_id})
                    log(f"  ⚠️  Erro ao processar item: {e_item}")
                    db.liberar_claim(produto_id)
                    funil.marcar("erros")
                    continue

        log(f"\n{'=' * 55}")
        log(f"Magalu: {publicados} oferta(s) publicada(s)")

        if funil is not None:
            try:
                funil.fechar(log)
            except Exception as _e_funil:
                log(f"⚠️  Funil não registrado: {_e_funil}")

        try:
            from core.metrics import inc  # noqa: PLC0415
            inc("rodadas_completadas")
        except Exception:
            pass

        if publicados:
            try:
                from core.site_publisher import publicar_site  # noqa: PLC0415
                publicar_site(origem="rastreador-magalu")
            except Exception as _e_site:
                from core.error_logger import log_erro  # noqa: PLC0415
                log_erro("site_publisher_falhou", _e_site, {"origem": "rastreador-magalu"})
    finally:
        db.finalizar_execucao(
            exec_id,
            produtos_encontrados=len(produtos),
            publicados=publicados,
        )


def _outra_instancia_magalu() -> bool:
    """True se já há outro rastreador_magalu em loop.

    Sem psutil a resposta honesta é "não sei", e aqui "não sei" tem que
    virar "não sobe outro": o mesmo raciocínio do supervisor na Regra 15 —
    agir sobre um False cego duplicaria o processo.
    """
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return True
    meu = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == meu:
                continue
            cmd = " ".join(p.info.get("cmdline") or [])
            nome = (p.info.get("name") or "").lower()
            if "rastreador_magalu.py" in cmd and "python" in nome and \
               ("--loop" in cmd or "--random" in cmd):
                return True
        except Exception:
            continue
    return False


def main() -> None:
    import random  # noqa: PLC0415
    parser = argparse.ArgumentParser(description="Rastreador de ofertas Magazine Luiza")
    parser.add_argument("--loop", type=int, metavar="MINUTOS")
    parser.add_argument("--random", action="store_true")
    parser.add_argument("--loop-min", type=int, default=45)
    parser.add_argument("--loop-max", type=int, default=75)
    args = parser.parse_args()

    if args.loop or args.random:
        if _outra_instancia_magalu():
            log("⛔ Outro rastreador_magalu já está rodando (ou psutil ausente). Encerrando.")
            return
        modo = (f"aleatório {args.loop_min}-{args.loop_max}min" if args.random
                else f"a cada {args.loop} min")
        log(f"Modo contínuo: {modo}. Ctrl+C para parar.")
        while True:
            try:
                asyncio.run(rodar_uma_vez())
            except Exception as e:
                from core.error_logger import log_erro  # noqa: PLC0415
                log_erro("magalu.rodada_falhou", e, {})
                log(f"⚠️  Rodada Magalu falhou inesperadamente: {e}")
            proximo = (random.randint(args.loop_min, args.loop_max)
                       if args.random else args.loop)
            log(f"\n⏳ Próxima rodada Magalu em {proximo} minuto(s)...")
            time.sleep(proximo * 60)
    else:
        asyncio.run(rodar_uma_vez())


if __name__ == "__main__":
    main()
