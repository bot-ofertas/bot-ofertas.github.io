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

# Carrega .env explicitamente para healthcheck rodando em thread separada
try:
    from dotenv import load_dotenv  # noqa: PLC0415
    load_dotenv(os.path.join(_BASE, ".env"))
except Exception:
    pass


def _status_chrome() -> dict:
    from core.chrome_manager import esta_pronto  # noqa: PLC0415
    return {"ok": esta_pronto(), "porta": 9222}


def _status_telegram() -> dict:
    # Recarrega .env por precaução (thread do healthcheck às vezes perde env)
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv(os.path.join(_BASE, ".env"))
    except Exception:
        pass
    tok = os.getenv("TOKEN_TELEGRAM", "")
    canal = os.getenv("CANAL_GERAL", "")
    return {"ok": bool(tok and canal), "canal": bool(canal)}


def _status_whatsapp() -> dict:
    """Retorna o melhor método de envio disponível para WhatsApp."""
    # 1º: Evolution API (headless, mais confiável)
    try:
        from integrations.whatsapp_api import _configurada, esta_conectada  # noqa: PLC0415
        if _configurada():
            if esta_conectada():
                return {"ok": True, "metodo": "evolution-api"}
            return {"ok": False, "motivo": "evolution-desconectada"}
    except Exception:
        pass
    # 2º: WhatsApp Desktop (nativo)
    try:
        from integrations.whatsapp_desktop import _processo_wa_rodando  # noqa: PLC0415
        if _processo_wa_rodando():
            return {"ok": True, "metodo": "desktop"}
        return {"ok": False, "motivo": "desktop-fechado"}
    except Exception as e:
        return {"ok": False, "motivo": str(e)[:80]}


def _status_rastreador() -> dict:
    """Status do rastreador — usa processo + log combinados.

    Considera ok se:
      - Existe processo python rodando rastreador.py --loop OU --random
      - Log tem menos de 55 min (cobrindo intervalo aleatório 30-45 min + margem)
    """
    log_path = os.path.join(_BASE, "data", "rastreador_local.log")
    processo_vivo = False
    try:
        import psutil  # noqa: PLC0415
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                cl = p.info.get("cmdline") or []
                n = (p.info.get("name") or "").lower()
                if "python" in n and any(a.endswith("rastreador.py") for a in cl):
                    processo_vivo = True
                    break
            except Exception:
                continue
    except ImportError:
        pass

    if not os.path.exists(log_path):
        return {"ok": processo_vivo, "motivo": "sem-log", "processo": processo_vivo}
    try:
        idade = time.time() - os.path.getmtime(log_path)
        # 55 min = cobre intervalo aleatório 30-45min + tempo de rodada + margem
        log_recente = idade < 3300
        return {
            "ok": processo_vivo and log_recente,
            "idade_s": round(idade),
            "processo": processo_vivo,
        }
    except Exception:
        return {"ok": processo_vivo}


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

    def _resp(self, code: int, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")  # para n8n
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            payload = {
                "chrome": _status_chrome(),
                "whatsapp": _status_whatsapp(),
                "telegram": _status_telegram(),
                "rastreador": _status_rastreador(),
                "sistema": _status_sistema(),
            }
            overall_ok = all([
                payload["chrome"]["ok"] or True,
                payload["telegram"]["ok"],
                payload["rastreador"]["ok"],
            ])
            self._resp(200 if overall_ok else 503, payload)
            return
        if self.path.startswith("/errors"):
            # /errors?limit=50 — últimos erros em JSON (para n8n)
            from urllib.parse import urlparse, parse_qs  # noqa: PLC0415
            q = parse_qs(urlparse(self.path).query)
            limite = int(q.get("limit", ["50"])[0])
            from core.error_logger import erros_recentes  # noqa: PLC0415
            self._resp(200, {"erros": erros_recentes(limite)})
            return
        if self.path == "/stats":
            # Estatísticas para n8n dashboard
            try:
                from core import database as db  # noqa: PLC0415
                self._resp(200, db.stats())
            except Exception as e:
                self._resp(500, {"error": str(e)})
            return
        if self.path == "/metrics":
            # Formato Prometheus para Grafana/n8n
            try:
                from core.metrics import formato_prometheus  # noqa: PLC0415
                body = formato_prometheus().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._resp(500, {"error": str(e)})
            return
        if self.path == "/cache":
            # Estatísticas do cache de fotos
            try:
                from core.foto_cache import stats  # noqa: PLC0415
                self._resp(200, stats())
            except Exception as e:
                self._resp(500, {"error": str(e)})
            return
        self._resp(404, {"error": "not found",
                         "endpoints": ["/health", "/errors", "/stats",
                                       "/metrics", "/cache", "POST /oferta"]})

    def do_POST(self):
        """POST /oferta — recebe oferta manual via n8n/webhook para postar."""
        if self.path != "/oferta":
            self._resp(404, {"error": "not found"})
            return
        try:
            tamanho = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(tamanho).decode("utf-8"))
            # Validação mínima
            campos_req = ("titulo", "preco", "link")
            faltando = [c for c in campos_req if not body.get(c)]
            if faltando:
                self._resp(400, {"error": "faltam campos", "campos": faltando})
                return
            # Publica assíncrono
            import threading  # noqa: PLC0415
            threading.Thread(
                target=_publicar_webhook, args=(body,), daemon=True
            ).start()
            try:
                from core.metrics import inc  # noqa: PLC0415
                inc("webhook_ofertas_recebidas")
            except Exception:
                pass
            self._resp(202, {"status": "aceito", "titulo": body.get("titulo")})
        except Exception as e:
            self._resp(500, {"error": str(e)})


def _publicar_webhook(produto: dict) -> None:
    """Publica oferta recebida via webhook nos canais configurados."""
    import asyncio
    import os
    async def _fluxo():
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv()
        from telegram import Bot  # noqa: PLC0415
        from integrations.telegram_bot import publicar  # noqa: PLC0415
        from integrations.whatsapp_sender import enviar_para_grupo  # noqa: PLC0415
        # Defaults
        produto.setdefault("categoria", "webhook")
        produto.setdefault("fonte", "webhook")
        canais = {"geral": os.getenv("CANAL_GERAL", "")}
        token = os.getenv("TOKEN_TELEGRAM", "")
        try:
            async with Bot(token=token) as bot:
                await publicar(bot, produto, canais)
                try:
                    from core.metrics import inc  # noqa: PLC0415
                    inc("posts_telegram_total")
                except Exception:
                    pass
        except Exception as e:
            log.warning("webhook telegram: %s", e)
        try:
            if await enviar_para_grupo(produto):
                from core.metrics import inc  # noqa: PLC0415
                inc("posts_whatsapp_total")
        except Exception as e:
            log.warning("webhook whatsapp: %s", e)
    asyncio.run(_fluxo())


def _servir():
    try:
        ThreadingHTTPServer(("127.0.0.1", PORTA), _Handler).serve_forever()
    except Exception as e:
        log.warning("Healthcheck não iniciou: %s", e)


def iniciar_healthcheck() -> None:
    threading.Thread(target=_servir, name="healthcheck", daemon=True).start()
    log.info("Healthcheck em http://127.0.0.1:%d/health", PORTA)
