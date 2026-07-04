# -*- coding: utf-8 -*-
"""
ERROR LOGGER — grava erros em JSON estruturado para debug e integração externa.

Cada erro é gravado em data/errors.jsonl (uma linha JSON por erro) com:
  timestamp, level, module, function, file, line, exception, traceback, context

Também registra no data/bot.log tradicional (texto).

Uso:
    from core.error_logger import setup_logging, log_erro
    setup_logging()  # 1x no início do processo
    try:
        ...
    except Exception as e:
        log_erro("envio_whatsapp", e, contexto={"grupo": "Bot-Ofertas"})
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE, "data")
os.makedirs(LOG_DIR, exist_ok=True)

TXT_LOG = os.path.join(LOG_DIR, "bot.log")
JSON_LOG = os.path.join(LOG_DIR, "errors.jsonl")


def setup_logging(nivel: int = logging.INFO) -> None:
    """Configura logging estruturado (idempotente)."""
    root = logging.getLogger()
    if getattr(root, "_bot_configured", False):
        return
    root.setLevel(nivel)

    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s [%(filename)s:%(lineno)d] %(message)s"
    )
    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # Arquivo texto rotativo (5MB x 5)
    fh = RotatingFileHandler(TXT_LOG, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    root._bot_configured = True  # type: ignore[attr-defined]
    logging.getLogger("bot").info("Log inicializado — txt=%s json=%s", TXT_LOG, JSON_LOG)


def log_erro(operacao: str, exc: BaseException, contexto: dict | None = None) -> None:
    """Grava um erro estruturado em JSON (para n8n consumir) + log texto.

    Args:
        operacao: identificador da operação (ex: 'envio_whatsapp', 'scrap_ml').
        exc: exceção capturada.
        contexto: dict com dados adicionais (produto, canal, url etc.).
    """
    frame = inspect.stack()[1]
    entrada = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "operacao": operacao,
        "exception": type(exc).__name__,
        "mensagem": str(exc)[:500],
        "arquivo": os.path.basename(frame.filename),
        "funcao": frame.function,
        "linha": frame.lineno,
        "contexto": contexto or {},
        "traceback": traceback.format_exc(limit=5).splitlines()[-8:],
    }
    # JSONL (uma linha por erro — fácil de tail e n8n consumir)
    try:
        with open(JSON_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # Texto tradicional
    logging.getLogger("bot").error(
        "[%s] %s: %s @ %s:%d ctx=%s",
        operacao, entrada["exception"], entrada["mensagem"],
        entrada["arquivo"], entrada["linha"], entrada["contexto"],
    )


def erros_recentes(limite: int = 50) -> list[dict]:
    """Retorna os últimos N erros como lista de dicts (para /health/errors)."""
    if not os.path.exists(JSON_LOG):
        return []
    try:
        with open(JSON_LOG, encoding="utf-8") as f:
            linhas = f.readlines()[-limite:]
        return [json.loads(l) for l in linhas if l.strip()]
    except Exception:
        return []
