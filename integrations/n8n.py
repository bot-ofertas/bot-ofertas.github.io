# -*- coding: utf-8 -*-
"""
N8N — barramento de eventos do bot para o n8n (nuvem ou self-hosted).

Por que PUSH e não POLL
───────────────────────
O n8n na nuvem (n8n.cloud) não tem como abrir conexão para o PC do Daniel:
o healthcheck vive em `127.0.0.1:8724`, atrás do roteador doméstico, com IP
dinâmico. Qualquer desenho baseado em "o n8n consulta o bot" exige túnel,
porta aberta ou VPS — configuração frágil que quebra na primeira troca de
IP. Aqui o bot é quem fala: cada evento vira um POST para um webhook do n8n.
Funciona com o n8n na nuvem sem abrir NADA no roteador.

O caminho inverso (n8n mandando comando para o bot) existe e é opcional —
ver `integrations/n8n_commands.py` e `POST /n8n/comando` no healthcheck.

Garantias
─────────
- **Nunca derruba nem atrasa uma publicação.** O envio roda em thread
  daemon; `emitir()` retorna na hora. Regra 6 do CLAUDE.md (Telegram não
  depende de nada externo) vale igual aqui.
- **Não perde evento em queda de rede.** Falhou o POST → o evento vai para
  `data/n8n_spool.jsonl` e é reenviado no próximo evento bem-sucedido. Isso
  cobre exatamente a queda intermitente de DNS vista nos logs de agosto.
- **Autenticado.** Cada requisição leva `X-Bot-Assinatura: sha256=<hmac>`
  do corpo exato, com o segredo `N8N_TOKEN`. O workflow do n8n recusa o que
  não bater — um webhook público sem isso aceitaria oferta de qualquer um.

Configuração (.env):
    N8N_WEBHOOK_URL=https://SEU-N8N/webhook/bot-ofertas
    N8N_TOKEN=um-segredo-longo-e-aleatorio
    N8N_ATIVO=1
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import socket
import threading
import time
from datetime import datetime

log = logging.getLogger("n8n")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPOOL_PATH = os.path.join(_BASE, "data", "n8n_spool.jsonl")

# Um spool sem teto vira um arquivo de gigabytes depois de um fim de semana
# com o n8n fora do ar. Passando disso, os mais antigos são descartados —
# evento de monitoramento velho não tem valor operacional.
SPOOL_MAX_LINHAS = 500

TIMEOUT_S = 8
TENTATIVAS = 2

_lock = threading.Lock()
_ultimo_envio: dict = {"ts": None, "evento": None, "ok": None, "erro": ""}


# ── Configuração ─────────────────────────────────────────────────────────────

def _url() -> str:
    return (os.getenv("N8N_WEBHOOK_URL") or "").strip().rstrip("/")


def _token() -> str:
    return (os.getenv("N8N_TOKEN") or "").strip()


def ativo() -> bool:
    """True se a integração está configurada E habilitada.

    Sem `N8N_WEBHOOK_URL` tudo aqui vira no-op silencioso — o bot roda
    exatamente como antes em qualquer máquina que não use n8n.
    """
    if os.getenv("N8N_ATIVO", "1").strip().lower() in ("0", "false", "nao", "não"):
        return False
    return bool(_url())


def assinar(corpo: bytes, token: str = "") -> str:
    """HMAC-SHA256 do corpo exato, no formato `sha256=<hex>`."""
    segredo = (token or _token()).encode("utf-8")
    return "sha256=" + hmac.new(segredo, corpo, hashlib.sha256).hexdigest()


def conferir_assinatura(corpo: bytes, assinatura: str, token: str = "") -> bool:
    """Valida assinatura recebida — comparação em tempo constante."""
    if not assinatura:
        return False
    return hmac.compare_digest(assinar(corpo, token), assinatura.strip())


# ── Envio ────────────────────────────────────────────────────────────────────

def _montar(evento: str, dados: dict | None) -> dict:
    return {
        "evento": evento,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "origem": "bot_ofertas",
        "dados": dados or {},
    }


def _postar(payload: dict) -> bool:
    """POST único do payload. True se o n8n aceitou (2xx)."""
    from core.net import FalhaDeRede, post  # noqa: PLC0415

    corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    cabecalhos = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Bot-Evento": payload.get("evento", ""),
        "X-Bot-Assinatura": assinar(corpo),
        "Accept": "application/json",
    }
    # O nó Webhook do n8n autentica por "Header Auth" (uma credencial com
    # nome e valor de cabeçalho). Mandamos o token cru NESSE formato além do
    # HMAC: assim o próprio n8n rejeita requisição não autenticada antes de
    # executar qualquer nó, sem precisar de um nó de verificação em JS nem
    # do segredo escrito dentro do workflow.
    if _token():
        cabecalhos["X-Bot-Token"] = _token()
    try:
        r = post(
            _url(), data=corpo, headers=cabecalhos,
            tentativas=TENTATIVAS, timeout=TIMEOUT_S,
        )
    except FalhaDeRede as e:
        _registrar_resultado(payload, False, str(e))
        return False
    ok = 200 <= r.status_code < 300
    _registrar_resultado(payload, ok, "" if ok else f"HTTP {r.status_code}")
    if not ok:
        log.warning("n8n recusou evento %s: HTTP %d", payload.get("evento"), r.status_code)
    return ok


def _registrar_resultado(payload: dict, ok: bool, erro: str) -> None:
    with _lock:
        _ultimo_envio.update({
            "ts": payload.get("ts"),
            "evento": payload.get("evento"),
            "ok": ok,
            "erro": erro[:200],
        })


# ── Spool (eventos que não saíram) ───────────────────────────────────────────

def _guardar_no_spool(payload: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SPOOL_PATH), exist_ok=True)
        with _lock:
            with open(SPOOL_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            _aparar_spool()
    except Exception as e:  # nunca propaga — spool é best-effort
        log.debug("Falha ao gravar spool do n8n: %s", e)


def _aparar_spool() -> None:
    """Mantém só as últimas SPOOL_MAX_LINHAS linhas. Chamado com _lock."""
    try:
        with open(SPOOL_PATH, encoding="utf-8") as f:
            linhas = f.readlines()
        if len(linhas) > SPOOL_MAX_LINHAS:
            with open(SPOOL_PATH, "w", encoding="utf-8") as f:
                f.writelines(linhas[-SPOOL_MAX_LINHAS:])
    except FileNotFoundError:
        pass


def tamanho_spool() -> int:
    try:
        with open(SPOOL_PATH, encoding="utf-8") as f:
            return sum(1 for linha in f if linha.strip())
    except FileNotFoundError:
        return 0
    except Exception:
        return -1


def flush_spool(limite: int = 50) -> int:
    """Reenvia eventos guardados. Devolve quantos saíram.

    Para na primeira falha e mantém o restante no arquivo — se a rede ainda
    está fora, insistir nos 500 só gasta a rodada.
    """
    if not ativo():
        return 0
    try:
        with _lock:
            with open(SPOOL_PATH, encoding="utf-8") as f:
                linhas = [linha for linha in f if linha.strip()]
    except FileNotFoundError:
        return 0
    except Exception:
        return 0

    enviados = 0
    for linha in linhas[:limite]:
        try:
            payload = json.loads(linha)
        except json.JSONDecodeError:
            enviados += 1  # linha corrompida: descarta junto com as enviadas
            continue
        if not _postar(payload):
            break
        enviados += 1

    if enviados:
        restantes = linhas[enviados:]
        try:
            with _lock:
                with open(SPOOL_PATH, "w", encoding="utf-8") as f:
                    f.writelines(restantes)
        except Exception as e:
            log.debug("Falha ao reescrever spool: %s", e)
        log.info("n8n: %d evento(s) do spool reenviados, %d restante(s)",
                 enviados, len(restantes))
    return enviados


# ── API pública ──────────────────────────────────────────────────────────────

def emitir(evento: str, dados: dict | None = None, *, bloqueante: bool = False) -> bool:
    """Envia um evento ao n8n.

    Por padrão devolve o controle imediatamente (thread daemon) — nenhuma
    publicação espera pelo n8n. `bloqueante=True` só nos testes e no CLI.
    Retorna False quando a integração está desligada.
    """
    if not ativo():
        return False
    payload = _montar(evento, dados)

    def _trabalho() -> bool:
        ok = _postar(payload)
        if ok:
            flush_spool()
        else:
            _guardar_no_spool(payload)
        return ok

    if bloqueante:
        return _trabalho()
    threading.Thread(target=_trabalho, name=f"n8n-{evento}", daemon=True).start()
    return True


def heartbeat(extra: dict | None = None) -> bool:
    """Pulso de vida — é ele que sustenta o watchdog na nuvem.

    O workflow `02-watchdog` guarda o horário do último heartbeat; se ele
    envelhecer além do limite, o n8n avisa que o bot caiu. Como o aviso
    nasce FORA do PC, ele funciona justamente no caso em que o bot (ou a
    máquina) morreu — que é quando um alerta gerado pelo próprio bot nunca
    chegaria.
    """
    dados = {"spool": tamanho_spool()}
    try:
        import core.database as db  # noqa: PLC0415
        dados["fila_whatsapp"] = db.tamanho_fila_whatsapp()
        dados["erros_10min"] = db.erros_ultima_janela(10)
        dados["quarentena"] = len(db.listar_quarentena(limite=50))
    except Exception as e:
        dados["db_erro"] = str(e)[:120]
    if extra:
        dados.update(extra)
    return emitir("heartbeat", dados)


def iniciar_heartbeat(intervalo_s: int = 300) -> None:
    """Sobe a thread de heartbeat (idempotente por nome de thread)."""
    if not ativo():
        log.info("n8n desativado (sem N8N_WEBHOOK_URL) — heartbeat não iniciado.")
        return
    if any(t.name == "n8n-heartbeat" for t in threading.enumerate()):
        return

    def _loop() -> None:
        while True:
            try:
                heartbeat()
            except Exception as e:
                log.debug("heartbeat falhou: %s", e)
            time.sleep(intervalo_s)

    threading.Thread(target=_loop, name="n8n-heartbeat", daemon=True).start()
    log.info("n8n: heartbeat a cada %ds para %s", intervalo_s, _url()[:60])


def status() -> dict:
    """Resumo da integração — entra no /health."""
    return {
        "ativo": ativo(),
        "url": _url()[:80],
        "autenticado": bool(_token()),
        "spool_pendente": tamanho_spool(),
        "ultimo_envio": dict(_ultimo_envio),
    }


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_BASE, ".env"))
    except ImportError:
        pass
    if not ativo():
        raise SystemExit("N8N_WEBHOOK_URL não configurada no .env")
    print("Enviando evento de teste para", _url())
    ok = emitir("teste", {"mensagem": "ping do bot_ofertas"}, bloqueante=True)
    print("Resultado:", "✅ aceito" if ok else "❌ recusado", "|", _ultimo_envio)
