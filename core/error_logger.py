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
    # Console — força UTF-8 no stdout antes de anexar o handler. Sem isso,
    # qualquer log com emoji/acento fora do codepage do console do Windows
    # (cp1252/cp437, dependendo do terminal) falha silenciosamente: a
    # exceção não derruba o processo, mas a mensagem de log é perdida e um
    # traceback "--- Logging error ---" polui o stderr no lugar dela.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
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
    # Espelho para o n8n (workflow 01 decide se vira alerta)
    _espelhar_no_n8n(entrada)


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
        erro_txt = f"{e['exception']}: {e['mensagem']}" if e['exception'] else e['mensagem']
        f.write(f"❌ Erro     : {erro_txt}\n")
        if e.get("arquivo"):
            f.write(f"📍 Onde     : {e['arquivo']} → função {e['funcao']}(), linha {e['linha']}\n")
        if e.get("contexto"):
            ctx_str = ", ".join(f"{k}={v}" for k, v in e["contexto"].items())
            f.write(f"📝 Contexto : {ctx_str}\n")
        if e.get("traceback"):
            f.write("🔍 Traceback:\n")
            for linha in e["traceback"]:
                f.write(f"     {linha}\n")
        f.write("\n")


def registrar_evento(operacao: str, mensagem: str, contexto: dict | None = None) -> None:
    """Mesmo destino de log_erro() (JSONL + bloco de notas do Desktop), mas
    para falhas reportadas como condição de negócio (retorno False), não uma
    exceção Python capturada -- não força um objeto de exceção falso só pra
    reusar log_erro().

    Achado ao vivo em 2026-08-24: db.registrar_erro() (usado por ex. em
    "falha ao publicar" do Telegram) só gravava na tabela erros_log do
    banco, nunca no bloco de notas do Desktop nem no errors.jsonl que
    log_erro() alimenta -- dois sistemas de log paralelos, sem se falar.
    Um surto real de 31 falhas de publicação numa janela de ~21min
    (2026-08-23 22:36-22:57) ficou invisível no bloco de notas por causa
    disso. registrar_erro() agora chama esta função também, unificando os
    dois num só lugar que o usuário realmente olha."""
    entrada = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "operacao": operacao,
        "exception": "",
        "mensagem": str(mensagem)[:500],
        "arquivo": "",
        "funcao": "",
        "linha": 0,
        "contexto": contexto or {},
        "traceback": [],
    }
    try:
        with open(JSON_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    except Exception:
        pass
    try:
        _gravar_desktop_txt(entrada)
    except Exception:
        pass
    _espelhar_no_n8n(entrada)


# ── Espelho para o n8n ───────────────────────────────────────────────────────

# Um surto de erro repetido (31 falhas em 21 min, 2026-08-23) viraria 31
# mensagens no Telegram. O throttle deixa passar 1 evento por operação a
# cada _JANELA_S — o suficiente pra saber que está acontecendo, sem
# transformar o alerta em ruído que ninguém lê. O registro completo continua
# indo pro errors.jsonl e pro bloco de notas, sem throttle nenhum.
_JANELA_THROTTLE_S = 300
_ultimo_evento_n8n: dict[str, float] = {}


def _espelhar_no_n8n(entrada: dict) -> None:
    """Envia o erro ao n8n (best-effort, com throttle por operação)."""
    import time  # noqa: PLC0415

    operacao = entrada.get("operacao", "")
    agora = time.time()
    if agora - _ultimo_evento_n8n.get(operacao, 0.0) < _JANELA_THROTTLE_S:
        return
    _ultimo_evento_n8n[operacao] = agora
    try:
        from integrations.n8n import emitir  # noqa: PLC0415
        emitir("erro", {
            "operacao": operacao,
            "exception": entrada.get("exception", ""),
            "mensagem": entrada.get("mensagem", ""),
            "arquivo": entrada.get("arquivo", ""),
            "linha": entrada.get("linha", 0),
            "contexto": entrada.get("contexto", {}),
        })
    except Exception:
        pass  # o n8n nunca pode atrapalhar o registro do erro


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
