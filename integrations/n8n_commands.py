# -*- coding: utf-8 -*-
"""
N8N COMMANDS — o caminho de volta: comandos do n8n para o bot.

`integrations/n8n.py` cuida do bot → n8n (push de eventos). Aqui é o
inverso: o n8n (ou um comando do Telegram encaminhado por ele) pede uma
ação ao bot. Chega por `POST /n8n/comando` no healthcheck, autenticado por
HMAC (mesmo `N8N_TOKEN` do push) ou pelo header `X-Bot-Token`.

Todo comando é uma função pura de `(dados) -> dict`, sem efeito colateral
fora do que ele declara. Comando desconhecido devolve erro explícito — o
handler nunca chama `eval`, `getattr` dinâmico em módulo, nem executa
string vinda da rede.
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger("n8n_commands")


def _cmd_status(_: dict) -> dict:
    import core.database as db  # noqa: PLC0415
    from core import pausa  # noqa: PLC0415
    from integrations import n8n  # noqa: PLC0415
    return {
        "stats": db.stats(),
        "pausado": pausa.pausado(),
        "pausa": pausa.info(),
        "fila_whatsapp": db.tamanho_fila_whatsapp(),
        "quarentena": len(db.listar_quarentena()),
        "n8n": n8n.status(),
    }


def _cmd_quarentena_listar(dados: dict) -> dict:
    import core.database as db  # noqa: PLC0415
    limite = int(dados.get("limite", 50))
    apenas_ativas = bool(dados.get("apenas_ativas", True))
    itens = db.listar_quarentena(limite=limite, apenas_ativas=apenas_ativas)
    return {"total": len(itens), "itens": itens}


def _cmd_quarentena_liberar(dados: dict) -> dict:
    import core.database as db  # noqa: PLC0415
    produto_id = str(dados.get("produto_id", "")).strip()
    todos = bool(dados.get("todos")) or produto_id in ("*", "todos")
    if not produto_id and not todos:
        # `db.liberar_quarentena("")` apaga a quarentena INTEIRA. Sem esta
        # guarda, um comando com o campo faltando ou escrito errado
        # ("produtoId") virava "libera tudo" — e os produtos que já
        # falharam 3x voltavam a queimar as 4 vagas de publicação da rodada,
        # que é exatamente o loop que a Regra 12 existe para fechar.
        # Desarmar a rede de segurança tem de ser explícito.
        raise ValueError(
            "informe produto_id, ou todos=true para liberar a quarentena inteira"
        )
    liberados = db.liberar_quarentena("" if todos else produto_id)
    log.info("Quarentena liberada via n8n: %s (%d)", produto_id or "TODOS", liberados)
    return {"liberados": liberados, "produto_id": produto_id or "*"}


def _cmd_pausar(dados: dict) -> dict:
    from core import pausa  # noqa: PLC0415
    return pausa.pausar(motivo=str(dados.get("motivo", ""))[:200], origem="n8n")


def _cmd_retomar(_: dict) -> dict:
    from core import pausa  # noqa: PLC0415
    return {"retomado": pausa.retomar()}


def _cmd_divulgacao(dados: dict) -> dict:
    from core import divulgacao  # noqa: PLC0415
    return divulgacao.gerar(
        rede=str(dados.get("rede", "instagram")),
        tipo=str(dados.get("tipo", "auto")),
        quantidade=int(dados.get("quantidade", 3)),
    )


def _cmd_erros(dados: dict) -> dict:
    from core.error_logger import erros_recentes  # noqa: PLC0415
    return {"erros": erros_recentes(int(dados.get("limite", 20)))}


def _cmd_flush_spool(_: dict) -> dict:
    from integrations import n8n  # noqa: PLC0415
    return {"reenviados": n8n.flush_spool()}


def _cmd_ping(dados: dict) -> dict:
    return {"pong": True, "eco": dados.get("eco", ""),
            "ts": datetime.now().isoformat(timespec="seconds")}


COMANDOS = {
    "status": _cmd_status,
    "quarentena_listar": _cmd_quarentena_listar,
    "quarentena_liberar": _cmd_quarentena_liberar,
    "pausar": _cmd_pausar,
    "retomar": _cmd_retomar,
    "divulgacao": _cmd_divulgacao,
    "erros": _cmd_erros,
    "flush_spool": _cmd_flush_spool,
    "ping": _cmd_ping,
}


def executar(comando: str, dados: dict | None = None) -> dict:
    """Executa um comando pelo nome. Nunca levanta — devolve `erro` no dict.

    O handler HTTP roda dentro do processo do healthcheck, que é thread
    daemon do startup: uma exceção não tratada aqui derrubaria a thread e
    tiraria o /health do ar junto.
    """
    fn = COMANDOS.get((comando or "").strip().lower())
    if not fn:
        return {"ok": False, "erro": "comando desconhecido",
                "disponiveis": sorted(COMANDOS)}
    try:
        return {"ok": True, "comando": comando, "resultado": fn(dados or {})}
    except Exception as e:
        log.warning("Comando n8n '%s' falhou: %s", comando, e)
        return {"ok": False, "comando": comando, "erro": f"{type(e).__name__}: {e}"[:300]}
