# -*- coding: utf-8 -*-
"""
METRICS — contadores persistidos em SQLite (compartilhados entre processos).

rastreador.py, rastreador_amazon.py e o healthcheck (dentro de startup.py)
rodam como processos do SO separados — contadores em memória pura ficam
isolados por processo, e o /metrics sempre mostraria zero para tudo que os
rastreadores incrementam. Por isso os contadores vivem na mesma
data/bot_ofertas.db (WAL), já compartilhada entre os processos para a
tabela de produtos.

Formato Prometheus (text-based) exposto em GET /metrics.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "bot_ofertas.db")
_iniciado_em = time.time()

_COUNTERS_PADRAO = (
    "posts_telegram_total", "posts_whatsapp_total", "posts_amazon_total",
    "posts_ml_total", "erros_total", "rodadas_completadas",
    "webhook_ofertas_recebidas",
)
_GAUGES_PADRAO = ("ultimo_post_ts", "ultima_rodada_duracao_s")


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    con = sqlite3.connect(_DB_PATH, check_same_thread=False, timeout=5)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS metricas ("
            "nome TEXT PRIMARY KEY, valor REAL NOT NULL DEFAULT 0)"
        )
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def inc(nome: str, valor: int = 1) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO metricas (nome, valor) VALUES (?, ?) "
            "ON CONFLICT(nome) DO UPDATE SET valor = valor + excluded.valor",
            (nome, valor),
        )


def set_gauge(nome: str, valor: float) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO metricas (nome, valor) VALUES (?, ?) "
            "ON CONFLICT(nome) DO UPDATE SET valor = excluded.valor",
            (nome, valor),
        )


def _ler_todas() -> dict[str, float]:
    with _conn() as con:
        rows = con.execute("SELECT nome, valor FROM metricas").fetchall()
    return dict(rows)


def snapshot() -> dict:
    dados = _ler_todas()
    return {
        "uptime_s": round(time.time() - _iniciado_em, 1),
        "counters": {k: int(dados.get(k, 0)) for k in _COUNTERS_PADRAO},
        "gauges": {k: dados.get(k, 0.0) for k in _GAUGES_PADRAO},
    }


def formato_prometheus() -> str:
    snap = snapshot()
    linhas = [
        "# HELP bot_uptime_seconds Tempo desde início do bot",
        "# TYPE bot_uptime_seconds gauge",
        f"bot_uptime_seconds {snap['uptime_s']}",
    ]
    for k, v in snap["counters"].items():
        linhas.append(f"# TYPE bot_{k} counter")
        linhas.append(f"bot_{k} {v}")
    for k, v in snap["gauges"].items():
        linhas.append(f"# TYPE bot_{k} gauge")
        linhas.append(f"bot_{k} {v}")
    return "\n".join(linhas) + "\n"
