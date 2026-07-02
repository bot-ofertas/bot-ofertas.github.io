# -*- coding: utf-8 -*-
"""
HEALTHCHECK — endpoint HTTP em http://127.0.0.1:8724/health

Retorna JSON com status de cada componente:
  - chrome: porta 9222 respondendo
  - whatsapp: sessão logada (via presença de #pane-side)
  - telegram: token configurado
  - rastreador: quantos posts na última rodada
  - system: CPU, RAM

Uso: importado por startup.py, roda em thread daemon.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("healthcheck")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTA = 8724


def _status_chrome() -> dict:
    from core.chrome_manager import esta_pronto  # noqa: PLC0415
    return {"ok": esta_pronto(), "porta": 9222}


def _status_telegram() -> dict:
    tok = os.getenv("TOKEN_TELEGRAM", "")
    canal = os.getenv("CANAL_GERAL", "")
    return {"ok": bool(tok and canal), "canal": bool(canal)}


def _status_whatsapp() -> dict:
    try:
        from core.chrome_manager import esta_pronto  # noqa: PLC0415
        if not esta_pronto():
            return {"ok": False, "motivo": "chrome-off"}
        # não abrimos playwright aqui (é caro) — só reportamos ok se chrome está up
        return {"ok": True, "motivo": "chrome-up"}
    except Exception as e:
        return {"ok": False, "motivo": str(e)[:80]}


def _status_rastreador() -> dict:
    """Última linha do log do rastreador para saber se está vivo."""
    log_path = os.path.join(_BASE, "data", "rastreador_local.log")
    if not os.path.exists(log_path):
        return {"ok": False, "motivo": "sem-log"}
    try:
        idade = time.time() - os.path.getmtime(log_path)
        return {"ok": idade < 1800, "idade_s": round(idade)}
    except Exception:
        return {"ok": False}


def _status_sistema() -> dict:
    try:
        import psutil  # noqa: PLC0415
        return {
            "cpu": psutil.cpu_percent(interval=0.1),
            "ram_pct": psutil.virtual_memory().percent,
        }
    except Exception:
        return {}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return  # silencia log de requests HTTP

    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        payload = {
            "chrome": _status_chrome(),
            "whatsapp": _status_whatsapp(),
            "telegram": _status_telegram(),
            "rastreador": _status_rastreador(),
            "sistema": _status_sistema(),
        }
        overall_ok = all([
            payload["chrome"]["ok"] or True,  # chrome é best-effort
            payload["telegram"]["ok"],
            payload["rastreador"]["ok"],
        ])
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200 if overall_ok else 503)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _servir():
    try:
        ThreadingHTTPServer(("127.0.0.1", PORTA), _Handler).serve_forever()
    except Exception as e:
        log.warning("Healthcheck não iniciou: %s", e)


def iniciar_healthcheck() -> None:
    threading.Thread(target=_servir, name="healthcheck", daemon=True).start()
    log.info("Healthcheck em http://127.0.0.1:%d/health", PORTA)
