# -*- coding: utf-8 -*-
"""
FILA DE ENVIO WHATSAPP — intervalo aleatório de 30-45 minutos.

Antes, o WhatsApp recebia cada oferta segundos depois do Telegram (mesmo
processo, mesma rodada) -- publicar nos dois lugares quase ao mesmo tempo,
sempre, é um padrão fácil de reconhecer como bot. Pedido do Daniel em
2026-08-24: desacoplar os dois. Telegram continua imediato (nunca depende
do WhatsApp); o WhatsApp passa a esperar nesta fila e sai sozinho, um item
por vez, num intervalo aleatório de 30 a 45 minutos.

Roda como processo separado, no mesmo padrão de rastreador.py e
campanha_ferramentas.py (ver startup.py). Consome core.database.fila_whatsapp,
alimentada por db.enfileirar_whatsapp() nos outros processos.
"""
from __future__ import annotations

import asyncio
import logging
import random
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from core.error_logger import setup_logging
setup_logging()

import core.database as db
from integrations.whatsapp_sender import enviar_para_grupo, wa_ativo

log = logging.getLogger("whatsapp_queue_sender")

# Cria a tabela fila_whatsapp se ainda não existir -- não confia que outro
# processo (rastreador.py etc.) já rodou isso primeiro, já que os 4
# processos sobem quase juntos no startup.py.
db.inicializar()

INTERVALO_MIN_S = 30 * 60
INTERVALO_MAX_S = 45 * 60

# Só 1 envio a cada 30-45min drena bem menos rápido do que o pipeline gera
# ofertas (60+/dia) -- sem limite de idade, a fila cresce sem parar e um dia
# manda uma oferta de horas atrás, com preço/estoque já mudado (o oposto do
# "mais credibilidade" pedido em 2026-08-18). Item mais velho que isso é
# descartado da fila (marcado como processado) em vez de enviado velho.
IDADE_MAX_S = 3 * 60 * 60


async def processar_fila_uma_vez() -> bool:
    """Envia o item mais antigo da fila que ainda esteja "fresco" (dentro de
    IDADE_MAX_S), descartando silenciosamente qualquer item mais velho que
    isso pelo caminho. Retorna True se enviou com sucesso."""
    if not wa_ativo():
        log.info("WhatsApp pausado (wa_ativo=False) — fila continua parada.")
        return False

    proximo = None
    while True:
        candidato = db.proximo_da_fila_whatsapp()
        if not candidato:
            return False
        fila_id_c, criado_em_c, item_c = candidato
        idade_s = (datetime.now() - datetime.fromisoformat(criado_em_c)).total_seconds()
        if idade_s > IDADE_MAX_S:
            log.info("⏭️  Descartando da fila (idade %.0fmin > limite): %s",
                     idade_s / 60, (item_c.get("titulo") or "")[:60])
            db.marcar_fila_whatsapp_enviado(fila_id_c)
            continue
        proximo = (fila_id_c, item_c)
        break

    fila_id, item = proximo
    override = item.pop("mensagem_override", None)
    log.info("📤 Enviando da fila: %s", (item.get("titulo") or "")[:60])
    try:
        ok = await asyncio.wait_for(
            enviar_para_grupo(item, mensagem_override=override), timeout=90.0,
        )
        log.info("Fila WhatsApp: %s", "✅ enviado" if ok else "falhou")
        if ok:
            try:
                from core.metrics import inc
                inc("posts_whatsapp_total")
            except Exception:
                pass
    except Exception as e:
        log.warning("Fila WhatsApp falhou: %s", e)
        ok = False

    # Marca como processado mesmo em falha -- item ruim (foto quebrada,
    # etc.) não deve travar a fila retentando pra sempre. Telegram já é a
    # fonte de verdade; WhatsApp aqui é best-effort, igual no resto do
    # projeto.
    db.marcar_fila_whatsapp_enviado(fila_id)
    return ok


async def main():
    log.info(
        "Fila de WhatsApp iniciada — intervalo aleatório de %d-%d min entre envios.",
        INTERVALO_MIN_S // 60, INTERVALO_MAX_S // 60,
    )
    while True:
        espera_s = random.uniform(INTERVALO_MIN_S, INTERVALO_MAX_S)
        pendentes = db.tamanho_fila_whatsapp()
        log.info("⏳ Próximo envio em %.1f min (fila: %d pendente(s)).", espera_s / 60, pendentes)
        await asyncio.sleep(espera_s)
        await processar_fila_uma_vez()


if __name__ == "__main__":
    asyncio.run(main())
