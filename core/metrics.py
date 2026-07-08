# -*- coding: utf-8 -*-
"""
METRICS — coleta métricas em memória para dashboard n8n/Grafana.

Formato Prometheus (text-based) exposto em GET /metrics.
Contadores simples: posts por canal, erros, tempo médio, etc.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_iniciado_em = time.time()

_counters: dict[str, int] = {
    "posts_telegram_total": 0,
    "posts_whatsapp_total": 0,
    "posts_amazon_total": 0,
    "posts_ml_total": 0,
    "erros_total": 0,
    "rodadas_completadas": 0,
    "webhook_ofertas_recebidas": 0,
}

_gauges: dict[str, float] = {
    "ultimo_post_ts": 0.0,
    "ultima_rodada_duracao_s": 0.0,
}


def inc(nome: str, valor: int = 1) -> None:
    with _lock:
        _counters[nome] = _counters.get(nome, 0) + valor


def set_gauge(nome: str, valor: float) -> None:
    with _lock:
        _gauges[nome] = valor


def snapshot() -> dict:
    with _lock:
        return {
            "uptime_s": round(time.time() - _iniciado_em, 1),
            "counters": dict(_counters),
            "gauges": dict(_gauges),
        }


def formato_prometheus() -> str:
    """Retorna as métricas no formato text-based do Prometheus."""
    linhas = [
        "# HELP bot_uptime_seconds Tempo desde início do bot",
        "# TYPE bot_uptime_seconds gauge",
        f"bot_uptime_seconds {round(time.time() - _iniciado_em, 1)}",
    ]
    with _lock:
        for k, v in _counters.items():
            linhas.append(f"# TYPE bot_{k} counter")
            linhas.append(f"bot_{k} {v}")
        for k, v in _gauges.items():
            linhas.append(f"# TYPE bot_{k} gauge")
            linhas.append(f"bot_{k} {v}")
    return "\n".join(linhas) + "\n"
