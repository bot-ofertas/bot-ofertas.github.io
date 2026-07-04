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

# Bloco de notas humano-legível na Área de Trabalho — para o usuário revisar
def _desktop_path() -> str:
    for env in ("USERPROFILE", "HOME"):
        p = os.environ.get(env)
        if p:
            d = os.path.join(p, "Desktop")
            if os.path.isdir(d):
                return d
            d = os.path.join(p, "Área de Trabalho")
            if os.path.isdir(d):
                return d
    return LOG_DIR  # fallback: grava em data/

DESKTOP_TXT = os.path.join(_desktop_path(), "Problemas de execução para corrigir.txt")


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
    # Bloco de notas humano-legível na Área de Trabalho
    try:
        _gravar_desktop_txt(entrada)
    except Exception:
        pass
    # Texto tradicional
    logging.getLogger("bot").error(
        "[%s] %s: %s @ %s:%d ctx=%s",
        operacao, entrada["exception"], entrada["mensagem"],
        entrada["arquivo"], entrada["linha"], entrada["contexto"],
    )


def _gravar_desktop_txt(e: dict) -> None:
    """Anexa o erro em formato humano-legível no bloco de notas do Desktop."""
    header_novo = not os.path.exists(DESKTOP_TXT)
    with open(DESKTOP_TXT, "a", encoding="utf-8") as f:
        if header_novo:
            f.write("=" * 78 + "\n")
            f.write("  PROBLEMAS DE EXECUÇÃO PARA CORRIGIR — Bot Ofertas\n")
            f.write("  Cada bloco abaixo é 1 erro que aconteceu no bot.\n")
            f.write("  Verifique 'operação', 'onde' e 'mensagem' para saber o que corrigir.\n")
            f.write("=" * 78 + "\n\n")
        f.write("─" * 60 + "\n")
        f.write(f"⏱️  Quando   : {e['ts']}\n")
        f.write(f"⚙️  Operação : {e['operacao']}\n")
        f.write(f"❌ Erro     : {e['exception']}: {e['mensagem']}\n")
        f.write(f"📍 Onde     : {e['arquivo']} → função {e['funcao']}(), linha {e['linha']}\n")
        if e.get("contexto"):
            ctx_str = ", ".join(f"{k}={v}" for k, v in e["contexto"].items())
            f.write(f"📝 Contexto : {ctx_str}\n")
        if e.get("traceback"):
            f.write("🔍 Traceback:\n")
            for linha in e["traceback"]:
                f.write(f"     {linha}\n")
        f.write("\n")


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
