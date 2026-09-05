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

import hmac
import json
import logging
import os
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("healthcheck")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTA = int(os.getenv("HEALTHCHECK_PORTA", "8724"))

# Endereço de escuta. Continua em 127.0.0.1 por padrão (nada exposto na
# rede sem decisão explícita). Um n8n self-hosted em contêiner na MESMA
# máquina não alcança o loopback do host — nesse caso, defina
# HEALTHCHECK_BIND=0.0.0.0 e proteja com N8N_TOKEN + firewall.
BIND = os.getenv("HEALTHCHECK_BIND", "127.0.0.1")

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


def _fila_whatsapp_pendentes() -> int:
    try:
        from core import database as db  # noqa: PLC0415
        return db.tamanho_fila_whatsapp()
    except Exception:
        return -1


def _status_whatsapp() -> dict:
    """Retorna o melhor método de envio disponível para WhatsApp.

    A PRIMEIRA pergunta é se existe destino configurado, não se o app está
    aberto. `whatsapp_sender.wa_ativo()` (que é `bool(WHATSAPP_GROUP_ID)`) é
    o que a fila consulta antes de cada envio: sem ele, o
    whatsapp_queue_sender registra "WhatsApp pausado (wa_ativo=False)" e não
    manda nada — para sempre. Enquanto este bloco olhava só o processo, o
    /health e o status.ps1 mostravam "WhatsApp: OK (desktop)" nesse exato
    cenário: verde na tela e zero postagem no grupo, sem nada explicando.
    """
    fila = _fila_whatsapp_pendentes()

    try:
        from integrations.whatsapp_sender import wa_ativo  # noqa: PLC0415
        if not wa_ativo():
            return {"ok": False, "metodo": "nenhum",
                    "motivo": "sem WHATSAPP_GROUP_ID no .env — a fila nao envia",
                    "fila_pendente": fila}
    except Exception as e:
        return {"ok": False, "motivo": f"nao consegui checar a config: {e}"[:80],
                "fila_pendente": fila}

    # 1º: Evolution API (headless, mais confiável)
    try:
        from integrations.whatsapp_api import _configurada, esta_conectada  # noqa: PLC0415
        if _configurada():
            if esta_conectada():
                return {"ok": True, "metodo": "evolution-api", "fila_pendente": fila}
            return {"ok": False, "motivo": "evolution-desconectada", "fila_pendente": fila}
    except Exception:
        pass
    # 2º: WhatsApp Desktop (nativo)
    try:
        from integrations.whatsapp_desktop import _processo_wa_rodando  # noqa: PLC0415
        if _processo_wa_rodando():
            return {"ok": True, "metodo": "desktop", "fila_pendente": fila}
        return {"ok": False, "motivo": "desktop-fechado", "fila_pendente": fila}
    except Exception as e:
        return {"ok": False, "motivo": str(e)[:80], "fila_pendente": fila}


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


def _status_erros() -> dict:
    try:
        import core.database as db  # noqa: PLC0415
        return {"ultimos_10min": db.erros_ultima_janela(10)}
    except Exception:
        return {}


def _status_ultimo_post() -> dict:
    try:
        from core.metrics import snapshot  # noqa: PLC0415
        ts = snapshot()["gauges"].get("ultimo_post_ts", 0)
        if not ts:
            return {"ts": None, "idade_s": None}
        return {"ts": ts, "idade_s": round(time.time() - ts, 1)}
    except Exception:
        return {}


def _status_n8n() -> dict:
    try:
        from integrations import n8n  # noqa: PLC0415
        return n8n.status()
    except Exception as e:
        return {"ativo": False, "erro": str(e)[:120]}


def _status_ml_token() -> dict:
    """Token da API do ML — renovável, em cache e quanto falta pra vencer.

    Sem isto, um token vencido só aparecia como erro 401 espalhado nos logs
    de scraping, horas depois de ter vencido.
    """
    try:
        from core.ml_token import status  # noqa: PLC0415
        return status()
    except Exception as e:
        return {"erro": str(e)[:120]}


def _status_pausa() -> dict:
    try:
        from core import pausa  # noqa: PLC0415
        return {"pausado": pausa.pausado(), **pausa.info()}
    except Exception:
        return {"pausado": False}


def _status_janela() -> dict:
    """Onde estamos no ciclo diário de liga/desliga do PC.

    Sem isso, um `/health` consultado às 03h não tem como distinguir "o bot
    morreu" de "o PC está desligado por agendamento" — e é essa diferença
    que decide entre acordar alguém e não fazer nada.
    """
    try:
        from core import janela  # noqa: PLC0415
        return janela.resumo()
    except Exception as e:
        return {"erro": str(e)[:120]}


def _status_papel() -> dict:
    """Qual publicador esta instância é, e se ela pode publicar agora.

    Com o bot também num servidor (deploy/), três processos passam a ser
    capazes de postar no mesmo canal: o PC, o GitHub Actions e o servidor.
    Sem esta linha no /health não há como responder "por que o servidor
    está quieto?" — a resposta certa costuma ser "porque o PC está ligado",
    e não "porque quebrou".
    """
    try:
        from core import papel  # noqa: PLC0415
        return papel.resumo()
    except Exception as e:
        return {"erro": str(e)[:120]}


def _status_quarentena() -> dict:
    """Produtos que falharam ao publicar e estão fora de rotação.

    Fica no /health de propósito: o caso real (MLB68674214, 5 falhas em 6h
    sem ninguém perceber) só era visível abrindo o relatório de erros.
    """
    try:
        from core import database as db  # noqa: PLC0415
        itens = db.listar_quarentena(limite=20)
        return {"total": len(itens),
                "produtos": [i["produto_id"] for i in itens][:10]}
    except Exception as e:
        return {"total": -1, "erro": str(e)[:120]}


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
                "erros": _status_erros(),
                "ultimo_post": _status_ultimo_post(),
                "n8n": _status_n8n(),
                "ml_token": _status_ml_token(),
                "pausa": _status_pausa(),
                "quarentena": _status_quarentena(),
                "janela": _status_janela(),
                "papel": _status_papel(),
            }
            # `payload["chrome"]["ok"] or True` estava na lista: constante
            # True, sem efeito nenhum -- o Chrome dedicado é opcional desde
            # que o WhatsApp passou a usar o app nativo (ver startup.py), e
            # a intenção era justamente NÃO deixá-lo reprovar a saúde. Fica
            # explícito agora, em vez de disfarçado de condição.
            criticos = {
                "telegram": payload["telegram"]["ok"],
                "rastreador": payload["rastreador"]["ok"],
            }
            overall_ok = all(criticos.values())
            payload["ok"] = overall_ok
            payload["criticos_com_falha"] = [k for k, v in criticos.items() if not v]
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
        if self.path == "/quedas":
            # Maiores quedas de preço no BD
            try:
                from core.price_alerts import listar_maiores_quedas  # noqa: PLC0415
                self._resp(200, listar_maiores_quedas(20))
            except Exception as e:
                self._resp(500, {"error": str(e)})
            return
        if self.path.startswith("/quarentena"):
            # Produtos fora de rotação por falha repetida de publicação.
            try:
                from urllib.parse import parse_qs, urlparse  # noqa: PLC0415
                from core import database as db  # noqa: PLC0415
                q = parse_qs(urlparse(self.path).query)
                todas = q.get("todas", ["0"])[0] not in ("0", "false", "")
                itens = db.listar_quarentena(
                    limite=int(q.get("limit", ["50"])[0]), apenas_ativas=not todas
                )
                self._resp(200, {"total": len(itens), "itens": itens})
            except Exception as e:
                self._resp(500, {"error": str(e)})
            return
        if self.path.startswith("/divulgacao"):
            # /divulgacao?rede=instagram&tipo=auto — texto pronto do anúncio
            # (o n8n busca aqui e publica; ver workflow 04-divulgacao-social).
            try:
                from urllib.parse import parse_qs, urlparse  # noqa: PLC0415
                from core import divulgacao  # noqa: PLC0415
                q = parse_qs(urlparse(self.path).query)
                self._resp(200, divulgacao.gerar(
                    rede=q.get("rede", ["instagram"])[0],
                    tipo=q.get("tipo", ["auto"])[0],
                    quantidade=int(q.get("qtd", ["3"])[0]),
                ))
            except Exception as e:
                self._resp(500, {"error": str(e)})
            return
        if self.path in ("/dashboard", "/dashboard/", "/"):
            # Dashboard HTML visual
            try:
                dash_path = os.path.join(_BASE, "core", "dashboard.html")
                with open(dash_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._resp(500, {"error": str(e)})
            return
        if self.path == "/feed.xml":
            # RSS feed das últimas ofertas — SEO grátis (agregadores/Zapier)
            try:
                from core import database as db  # noqa: PLC0415
                import html  # noqa: PLC0415
                produtos = db.listar_todos(limite=30)
                items = []
                for p in produtos:
                    if p.get("status") != "enviado":
                        continue
                    titulo = html.escape(p.get("titulo", "")[:120])
                    link = html.escape(p.get("affiliate_link") or "#")
                    desc = html.escape(
                        f"R$ {p.get('preco', 0):.2f} — "
                        f"{p.get('desconto_pct', 0):.0f}% OFF"
                    )
                    items.append(
                        f"<item><title>{titulo}</title>"
                        f"<link>{link}</link>"
                        f"<description>{desc}</description>"
                        f"<guid>{link}</guid></item>"
                    )
                xml = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<rss version="2.0"><channel>'
                    '<title>Bot Ofertas — Melhores promoções</title>'
                    '<link>https://bot-ofertas.github.io/</link>'
                    '<description>Ofertas com maior desconto</description>'
                    f'{"".join(items)}</channel></rss>'
                )
                body = xml.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._resp(500, {"error": str(e)})
            return
        self._resp(404, {"error": "not found",
                         "endpoints": [
                             "/dashboard", "/health", "/errors", "/stats",
                             "/metrics", "/cache", "/quedas", "/feed.xml",
                             "/quarentena", "/divulgacao",
                             "POST /oferta", "POST /alerta", "POST /n8n/comando",
                         ]})

    def _autorizado(self, corpo: bytes) -> bool:
        """Autentica um POST de comando.

        Duas formas aceitas, nessa ordem:
          1. `X-Bot-Assinatura: sha256=<hmac do corpo>` — mesma assinatura
             que o bot usa ao empurrar eventos, então o workflow do n8n
             reaproveita o segredo que já tem;
          2. `X-Bot-Token: <N8N_TOKEN>` — para um teste rápido com curl.

        Sem `N8N_TOKEN` configurado, só o próprio computador pode mandar
        comando. Isso importa porque `/n8n/comando` altera estado (pausa,
        quarentena) e `/oferta` publica nos grupos: deixá-los abertos sem
        segredo transformaria qualquer acesso à rede local num botão de
        desligar o bot — ou, pior, de postar no canal em nome dele.
        """
        from integrations import n8n  # noqa: PLC0415
        token = (os.getenv("N8N_TOKEN") or "").strip()
        if not token:
            return self.client_address[0] in ("127.0.0.1", "::1", "localhost")
        cabecalho_token = (self.headers.get("X-Bot-Token") or "").strip()
        if cabecalho_token and hmac.compare_digest(cabecalho_token, token):
            return True
        return n8n.conferir_assinatura(corpo, self.headers.get("X-Bot-Assinatura") or "")

    def _comando_n8n(self, corpo: bytes) -> None:
        try:
            body = json.loads(corpo.decode("utf-8") or "{}")
        except json.JSONDecodeError as e:
            self._resp(400, {"error": f"JSON inválido: {e}"})
            return
        from integrations.n8n_commands import executar  # noqa: PLC0415
        resultado = executar(body.get("comando", ""), body.get("dados") or {})
        self._resp(200 if resultado.get("ok") else 400, resultado)

    def _oferta_avulsa(self, corpo: bytes) -> None:
        try:
            body = json.loads(corpo.decode("utf-8") or "{}")
        except json.JSONDecodeError as e:
            self._resp(400, {"error": f"JSON inválido: {e}"})
            return
        campos_req = ("titulo", "preco", "link")
        faltando = [c for c in campos_req if not body.get(c)]
        if faltando:
            self._resp(400, {"error": "faltam campos", "campos": faltando})
            return
        try:
            import threading  # noqa: PLC0415
            threading.Thread(target=_publicar_webhook, args=(body,), daemon=True).start()
            try:
                from core.metrics import inc  # noqa: PLC0415
                inc("webhook_ofertas_recebidas")
            except Exception:
                pass
            self._resp(202, {"status": "aceito", "titulo": body.get("titulo")})
        except Exception as e:
            self._resp(500, {"error": str(e)})

    def _alerta(self, corpo: bytes) -> None:
        try:
            body = json.loads(corpo.decode("utf-8") or "{}")
            mensagem = body.get("mensagem", "")
            if not mensagem:
                self._resp(400, {"error": "faltando 'mensagem'"})
                return
            from core.error_logger import registrar_evento  # noqa: PLC0415
            registrar_evento(f"n8n.{body.get('origem', 'n8n')}", mensagem,
                             body.get("contexto"))
            self._resp(202, {"status": "registrado"})
        except Exception as e:
            self._resp(500, {"error": str(e)})

    def do_POST(self):
        """POST /oferta — recebe oferta manual via n8n/webhook para postar.
        POST /alerta — recebe um alerta do workflow de monitoramento do n8n
        e grava no mesmo bloco de notas do Desktop que o resto do sistema
        já usa (core.error_logger) -- evita precisar de mais um canal de
        aviso (chat pessoal do Telegram, e-mail etc.) só pra isso.
        POST /n8n/comando — ver `integrations/n8n_commands.py`.

        Os três passam pela MESMA porta de autenticação (`_autorizado`).
        Antes só `/n8n/comando` exigia segredo, e `/oferta` — que publica no
        canal do Telegram e no grupo do WhatsApp — ficava aberto: quem
        alcançasse a porta postava o link que quisesse como se fosse do
        Daniel. Pior, o n8n/README manda abrir `HEALTHCHECK_BIND=0.0.0.0`
        para o n8n em contêiner "protegendo com N8N_TOKEN" — proteção que
        não existia justamente no endpoint que fala com os grupos.
        """
        rotas = {
            "/n8n/comando": self._comando_n8n,
            "/oferta": self._oferta_avulsa,
            "/alerta": self._alerta,
        }
        handler = rotas.get(self.path)
        if handler is None:
            self._resp(404, {"error": "not found"})
            return
        try:
            tamanho = int(self.headers.get("Content-Length", 0))
            corpo = self.rfile.read(tamanho) if tamanho else b"{}"
        except Exception as e:
            self._resp(400, {"error": f"corpo ilegível: {e}"})
            return
        if not self._autorizado(corpo):
            self._resp(401, {"error": "nao autorizado",
                             "dica": "envie X-Bot-Assinatura (HMAC) ou X-Bot-Token"})
            return
        handler(corpo)


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
        ThreadingHTTPServer((BIND, PORTA), _Handler).serve_forever()
    except Exception as e:
        log.warning("Healthcheck não iniciou: %s", e)


def iniciar_healthcheck(com_n8n: bool = True) -> None:
    threading.Thread(target=_servir, name="healthcheck", daemon=True).start()
    log.info("Healthcheck em http://%s:%d/health", BIND, PORTA)
    if not com_n8n:
        return
    # Heartbeat para o n8n: é o que permite ao watchdog na nuvem perceber
    # que o bot morreu. Best-effort — se o n8n não estiver configurado,
    # iniciar_heartbeat() é no-op e o healthcheck segue igual.
    try:
        from integrations.n8n import iniciar_heartbeat  # noqa: PLC0415
        iniciar_heartbeat(int(os.getenv("N8N_HEARTBEAT_S", "300")))
    except Exception as e:
        log.warning("Heartbeat n8n não iniciou: %s (não crítico)", e)
