# -*- coding: utf-8 -*-
"""
CAMPANHA DEDICADA — Ferramentas para Construção Civil
========================================================
Busca ofertas de ferramentas (parafusadeiras, níveis a laser, malas de
ferramentas, furadeiras etc.) nas categorias Ferramentas + Construção do
Mercado Livre e publica pelo menos 6 por rodada, a cada 15 minutos.

Roda como processo separado do rastreador.py principal — MAX_POR_CATEGORIA=1
existe lá pra garantir DIVERSIDADE entre muitas categorias por rodada; essa
campanha é o oposto por natureza (volume alto numa única área), então usa
sua própria lógica de publicação em vez de reusar processar_categoria().

Regra de ouro igual ao resto do projeto: só publica com link oficial de
afiliado gerado (matt_tool=47114387).

Como usar:
    python campanha_ferramentas.py            → roda uma vez agora
    python campanha_ferramentas.py --loop 15  → a cada 15 minutos, contínuo
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import logging
import os
import re
import time
from datetime import datetime

from dotenv import load_dotenv
from telegram import Bot

from core.error_logger import setup_logging
setup_logging()
from core.scorer import score_inteligente
from core.validador import validar
import core.database as db
from affiliates.registry import get_provider
from integrations.ml_browser import (
    _extrair_produtos_json,
    _normalizar_dom,
    _DOM_SCRIPT,
    _filtrar_e_afiliar,
)
from integrations.telegram_bot import publicar, publicar_alerta_cupom
from integrations.social_poster import publicar_todas_redes, resumo_redes
from integrations.whatsapp_sender import wa_ativo
from integrations import n8n
from core import pausa
from core.net import dns_ok

load_dotenv()

TOKEN_TELEGRAM = os.getenv("TOKEN_TELEGRAM", "")
CANAIS = {"geral": os.getenv("CANAL_GERAL", "")}

MIN_POR_RODADA = 6
DESCONTO_MINIMO = 10
SCORE_MINIMO = 50  # um pouco mais permissivo que o padrão (60) pra sustentar volume
PAUSA_ENTRE_POSTS = 6

# Ferramentas em si + Construção (tem parafusadeiras/furadeiras/esmerilhadeiras
# de marcas como Bosch também, mas mistura com fechadura/cuba/luminária —
# por isso o filtro de palavra-chave abaixo).
URLS_FONTE = [
    ("ferramentas", "https://www.mercadolivre.com.br/c/ferramentas"),
    ("construcao", "https://www.mercadolivre.com.br/c/construcao"),
]

_PALAVRAS_FERRAMENTA = (
    "parafusadeira", "furadeira", "esmerilhadeira", "nivel", "nível", "laser",
    "maleta", "chave de fenda", "chave allen", "jogo de chave", "martelo",
    "serra ", "serra-", "alicate", "trena", "broca", "lixadeira",
    "kit ferramenta", "caixa de ferramenta", "furadeira de impacto",
    "parafusadeira e furadeira", "makita", "bosch", "dewalt", "vonder",
    "tramontina", "the black tools", "wap",
    # Ampliado em 2026-08-02: comparando os itens com desconto de
    # /c/ferramentas e /c/construcao contra o filtro, vários itens
    # claramente do nicho (lavadora de alta pressão Kärcher, compressor/
    # calibrador de pneu, máscara de solda, motosserra) só passavam quando
    # coincidiam com uma marca já listada — o termo genérico do próprio
    # produto não estava coberto. Termos abaixo capturam esses casos sem
    # abrir a porta pra itens fora do tema (fechadura, cuba, câmera, EPI
    # continuam de fora, pois nenhum deles bate com nada aqui).
    "ferramenta", "lavadora de alta pressao", "lavadora de alta pressão",
    "kärcher", "karcher", "compressor", "calibrador de pneu", "calibrador de ar",
    "solda", "soldagem", "soldador", "motosserra", "roçadeira", "rocadeira",
    "gedore", "irwin", "stanley", "black+decker", "black & decker", "schulz",
    "worker", "würth", "wurth", "milwaukee", "metabo", "einhell", "toyama",
    # Ampliado em 2026-08-03: Daniel pediu reforço específico para ferramentas
    # elétricas/kits A BATERIA de marcas renomadas (citou DeWalt, Ingco e
    # Makita como exemplos). DeWalt/Makita já cobertos; "ingco" era lacuna
    # confirmada. Marcas abaixo checadas uma a uma contra anúncios reais e
    # ativos no Mercado Livre Brasil, todas linhas de parafusadeira/furadeira
    # a bateria. Também testei se faltava termo genérico pra "kit/combo a
    # bateria" — não falta: "ferramenta"/"maleta" já cobrem esses títulos, e
    # "bateria" sozinha abriria falso-positivo grave (power bank, bateria
    # automotiva/notebook). "total", "hitachi", "tolsen", "bremen" avaliadas
    # e descartadas por risco de falso-positivo ou falta de evidência direta
    # de linha a bateria.
    "ingco", "skil", "ryobi", "worx", "lynus", "nakasaki", "wesco", "fortg",
    "hikoki", "kawasaki",
    # Ampliado em 2026-08-04: "aika" (marca brasileira de ferramenta a
    # bateria, confirmada com anúncios reais e ativos no ML — furadeira/
    # parafusadeira 20v com maleta + 2 baterias).
    "aika",
)

# Marcas renomadas de ferramenta A BATERIA (pediu prioridade específica pra
# dewalt/makita/aika/ingco "e outras renomadas" — mesmo espírito do pedido
# original de 2026-08-03). Usado só pra PRIORIZAR ordem de publicação, não
# pra filtrar — a inclusão continua vindo de _PALAVRAS_FERRAMENTA acima.
_MARCAS_BATERIA_RENOMADAS = (
    "dewalt", "makita", "aika", "ingco", "bosch", "milwaukee", "worx",
    "ryobi", "skil", "stanley", "black+decker", "black & decker",
    "metabo", "einhell", "hikoki",
)
_RE_VOLTAGEM_BATERIA = re.compile(r"\b\d{1,2}\s?v\b", re.IGNORECASE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def log(msg: str) -> None:
    print(msg)
    logging.info(msg)


def _ja_existe_outra_instancia() -> bool:
    """True se já houver outro campanha_ferramentas.py em modo --loop rodando.

    Mesma proteção do rastreador.py (_ja_existe_outra_instancia) — evita
    instâncias duplicadas que causariam posts repetidos (dedup aqui é
    check-then-act contra o SQLite, com trabalho lento — geração de link
    de afiliado + publicação — entre o check e a gravação; duas instâncias
    correndo juntas podem ambas passar pelo check antes de qualquer uma
    gravar).
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
                if "campanha_ferramentas.py" in cmd and "--loop" in cmd and "python" in nome:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass
    return False


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


def _e_ferramenta(item: dict) -> bool:
    titulo = (item.get("titulo") or "").lower()
    return any(p in titulo for p in _PALAVRAS_FERRAMENTA)


def _e_kit_bateria_marca_renomada(item: dict) -> bool:
    """Ferramenta/kit a bateria de marca renomada (dewalt, makita, aika,
    ingco e afins) — usado só como sinal de PRIORIDADE de publicação.

    Sem isso, o corte de MIN_POR_RODADA (ordenado só por % de desconto)
    podia deixar de fora um kit a bateria de marca boa que apareceu com
    desconto menor que itens genéricos, atrasando a postagem por rodadas
    seguidas em vez de sair na hora que a oferta é detectada."""
    titulo = (item.get("titulo") or "").lower()
    tem_marca = any(m in titulo for m in _MARCAS_BATERIA_RENOMADAS)
    tem_bateria = "bateria" in titulo or bool(_RE_VOLTAGEM_BATERIA.search(titulo))
    return tem_marca and tem_bateria


async def _buscar_pagina(url: str) -> list[dict]:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            # new_context() também precisa estar DENTRO do try — se falhar
            # (timeout, subprocesso do chromium crashado), o finally abaixo
            # ainda fecha o browser já lançado, em vez de vazar chromium.exe.
            ctx = await browser.new_context(
                locale="pt-BR",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 1024},
            )
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=40000)
            except PWTimeout:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                await page.wait_for_selector(
                    ".andes-card.poly-card, [class*='poly-card--grid']", timeout=10000
                )
            except PWTimeout:
                pass
            html = await page.content()
            produtos = _extrair_produtos_json(html)
            if not produtos:
                raw = await page.evaluate(_DOM_SCRIPT)
                produtos = _normalizar_dom(raw)
            return produtos
        finally:
            # Mesma proteção contra vazamento de chromium.exe do resto do projeto.
            try:
                await browser.close()
            except Exception:
                pass


async def rodar_uma_vez() -> int:
    db.inicializar()
    removidos = db.limpar_antigos(dias=2)
    if removidos:
        log(f"🧹 Limpeza automática: {removidos} produto(s) antigos removidos do banco")
    if not TOKEN_TELEGRAM:
        log("❌ TOKEN_TELEGRAM não definido no .env")
        return 0

    # Pausa global e pré-checagem de rede — mesma lógica do rastreador.py.
    # Sai ANTES de db.iniciar_execucao() para não deixar execução vazia no
    # histórico nem marcar "sistema ocupado" para o desligamento noturno.
    if pausa.pausado():
        log(f"⏸️  Publicação pausada ({pausa.info().get('motivo', '')}) — campanha não roda.")
        return 0
    if not dns_ok():
        log("🌐 Sem resolução de DNS — pulando a rodada da campanha.")
        db.registrar_erro("rede", "DNS indisponível — campanha pulada")
        n8n.emitir("rodada_pulada", {"motivo": "dns_indisponivel", "fonte": "ferramentas"})
        return 0

    # Registrado na mesma tabela `execucoes` do rastreador.py/rastreador_amazon.py
    # — é o que core.database.execucao_em_andamento() consulta pro desligamento
    # noturno saber que a campanha está no meio de uma rodada e esperar.
    exec_id = db.iniciar_execucao()

    log("\n" + "=" * 55)
    log(f"Campanha Ferramentas — {datetime.now().strftime('%H:%M:%S')}")

    candidatos: list[dict] = []
    for nicho, url in URLS_FONTE:
        try:
            brutos = await _buscar_pagina(url)
        except Exception as e:
            log(f"  ⚠️  Erro buscando {nicho}: {e}")
            db.registrar_erro("scraping", str(e))
            continue
        filtrados = _filtrar_e_afiliar(brutos, nicho, DESCONTO_MINIMO, limite=50)
        relevantes = [i for i in filtrados if _e_ferramenta(i)]
        log(f"  {nicho}: {len(brutos)} bruto(s) → {len(filtrados)} c/ desconto → {len(relevantes)} de ferramenta")
        candidatos.extend(relevantes)

    # Ordena com prioridade pra kit/ferramenta a bateria de marca renomada
    # (sempre primeiro, garantindo que entre no corte de MIN_POR_RODADA
    # assim que aparecer) e, dentro de cada grupo, por desconto (melhores
    # primeiro). Remove duplicatas entre as fontes.
    vistos: set[str] = set()
    unicos = []
    for item in sorted(
        candidatos,
        key=lambda i: (_e_kit_bateria_marca_renomada(i), i.get("desconto_pct", 0)),
        reverse=True,
    ):
        pid = _id_produto(item)
        if pid in vistos:
            continue
        vistos.add(pid)
        unicos.append(item)

    n_prioritarios = sum(1 for i in unicos if _e_kit_bateria_marca_renomada(i))
    if n_prioritarios:
        log(f"  🔋 {n_prioritarios} kit(s) a bateria de marca renomada nesta rodada (prioridade máxima)")

    publicados = 0
    # try/finally: sem isso, uma exceção não capturada em algum ponto do
    # loop deixava exec_id aberto pra sempre — confirmado ao vivo em
    # 2026-08-03 ("campanha_ferramentas_falhou: Timed out" com a execução
    # correspondente nunca finalizada). execucao_em_andamento() conta
    # qualquer execução aberta há menos de 20min como "sistema ocupado",
    # então uma rodada travada podia atrasar o desligamento noturno à toa.
    try:
        async with Bot(token=TOKEN_TELEGRAM) as bot:
            for item in unicos:
                if publicados >= MIN_POR_RODADA:
                    break

                produto_id = _id_produto(item)
                item["id"] = produto_id
                titulo_curto = (item.get("titulo") or "")[:55]

                try:
                    db.registrar_preco(produto_id, item.get("preco"))

                    if db.produto_id_existe(produto_id):
                        continue

                    aprovado, motivo = validar(item, reputacao={})
                    if not aprovado:
                        continue

                    score = score_inteligente(item)
                    item["score"] = score
                    if score < SCORE_MINIMO:
                        continue

                    url_original = item.get("link", "").split("?")[0].split("#")[0]
                    provider = get_provider(url_original)
                    if provider is None:
                        continue

                    # Reivindicação atômica — fecha a corrida com rastreador.py
                    # (roda como processo separado e escaneia a MESMA categoria
                    # "ferramentas"): sem isso, os dois podiam checar
                    # produto_id_existe() como False antes de qualquer um gravar
                    # no banco, e publicar o mesmo produto 2x no mesmo canal.
                    # Feita bem antes das duas chamadas de rede lentas (geração
                    # de link + publicação) que criam a janela de corrida de
                    # verdade.
                    if db.em_quarentena(produto_id):
                        log(f"  🚫 Em quarentena (falhas anteriores): {titulo_curto}")
                        continue

                    if not db.claim_produto(produto_id, item.get("titulo", "")):
                        continue

                    try:
                        link_afiliado = await provider.generate_affiliate_link_async(url_original)
                    except Exception as e:
                        log(f"     ❌ Erro ao gerar link: {e}")
                        db.registrar_erro("affiliate", str(e), produto_id)
                        db.liberar_claim(produto_id)
                        continue

                    if not link_afiliado or not provider.validate_affiliate_link(link_afiliado):
                        # Libera a reivindicação em vez de persistir com
                        # status='pendente' — essa falha costuma ser transitória
                        # e não deve congelar o produto por até 2 dias; o erro já
                        # fica registrado em erros_log via registrar_erro acima.
                        db.liberar_claim(produto_id)
                        continue

                    item["link"] = link_afiliado
                    item["categoria"] = "ferramentas"

                    tem_cupom = bool(item.get("cupom"))
                    if tem_cupom:
                        sucesso = await publicar_alerta_cupom(bot, item, CANAIS)
                    else:
                        sucesso = await publicar(bot, item, CANAIS)

                    if not sucesso:
                        db.registrar_erro("telegram", "falha ao publicar", produto_id)
                        falha = db.registrar_falha_publicacao(
                            produto_id, "falha ao publicar no Telegram",
                            item.get("titulo", ""),
                        )
                        if falha["quarentena"]:
                            # Mesmo tratamento do rastreador.py: esgotadas as
                            # tentativas, o produto sai de rotação em vez de
                            # voltar a cada 15 min consumindo a vaga da rodada.
                            item["status"] = "quarentena"
                            db.atualizar_produto(item)
                            log(f"  🚫 {falha['tentativas']}ª falha — quarentena até "
                                f"{falha['quarentena_ate'][:16]}: {titulo_curto}")
                            n8n.emitir("produto_quarentena", falha)
                        else:
                            db.liberar_claim(produto_id)
                        continue

                    item["status"] = "enviado"
                    item["adicionado_em"] = datetime.now().isoformat()
                    db.atualizar_produto(item)
                    db.atualizar_afiliado(produto_id, provider.name, link_afiliado, "ok")
                    db.marcar_enviado(produto_id)
                    db.limpar_falha_publicacao(produto_id)
                    n8n.emitir("oferta_publicada", {
                        "produto_id": produto_id,
                        "titulo": item.get("titulo"),
                        "preco": item.get("preco"),
                        "desconto_pct": item.get("desconto_pct"),
                        "categoria": "ferramentas",
                        "foto": item.get("foto"),
                        "link": link_afiliado,
                        "fonte": "mercadolivre",
                    })
                    publicados += 1
                    log(f"  ✅ ({publicados}/{MIN_POR_RODADA}) {titulo_curto} | {item.get('desconto_pct', 0):.0f}% OFF")

                    # WhatsApp entra na fila (intervalo aleatório 30-45min,
                    # ver whatsapp_queue_sender.py) em vez de sair junto com
                    # o Telegram -- mesmo motivo do rastreador.py.
                    if wa_ativo():
                        db.enfileirar_whatsapp(dict(item))
                        log(f"     💚 WhatsApp: na fila ({db.tamanho_fila_whatsapp()} pendente(s))")

                    try:
                        redes = await publicar_todas_redes(item)
                        if redes:
                            log(f"     🌐 Redes: {resumo_redes(redes)}")
                    except Exception as e:
                        log(f"     ⚠️  Social: {e}")

                    await asyncio.sleep(PAUSA_ENTRE_POSTS)
                except Exception as e_item:
                    log(f"  ⚠️  Erro inesperado em '{titulo_curto}': {e_item}")
                    db.registrar_erro("item_falhou", str(e_item), produto_id)
                    # Rede de segurança: libera a reivindicação se algo explodiu
                    # antes de chegar num estado terminal — vira no-op se já foi
                    # resolvido (liberar_claim só apaga linhas ainda 'processing').
                    db.liberar_claim(produto_id)
                    continue

        log(f"\nRodada concluída: {publicados} publicado(s) (meta: {MIN_POR_RODADA})")
        if publicados < MIN_POR_RODADA:
            log(
                "  ℹ️  Estoque de ofertas de ferramenta com desconto real disponível agora "
                "é menor que a meta — publicou o que havia sem repetir nem forçar itens fracos."
            )
    finally:
        db.finalizar_execucao(
            exec_id,
            produtos_encontrados=len(unicos),
            publicados=publicados,
        )
    return publicados


def main() -> None:
    parser = argparse.ArgumentParser(description="Campanha de ofertas de ferramentas para construção civil")
    parser.add_argument("--loop", type=int, metavar="MINUTOS", help="Roda em loop a cada N minutos (ex: --loop 15)")
    args = parser.parse_args()

    if args.loop:
        if _ja_existe_outra_instancia():
            log("⛔ Outro campanha_ferramentas --loop já está rodando. Encerrando para evitar duplicatas.")
            return
        log(f"Modo contínuo: a cada {args.loop} minuto(s). Ctrl+C para parar.")
        while True:
            try:
                asyncio.run(rodar_uma_vez())
            except Exception as e:
                log(f"⚠️  Rodada falhou inesperadamente: {e}")
                db.registrar_erro("campanha_ferramentas_falhou", str(e))
            log(f"\n⏳ Próxima rodada em {args.loop} minuto(s)...")
            time.sleep(args.loop * 60)
    else:
        asyncio.run(rodar_uma_vez())


if __name__ == "__main__":
    main()
