# -*- coding: utf-8 -*-
"""
WATCHDOG DO WHATSAPP DESKTOP

Roda em thread daemon. A cada 60s verifica se o processo nativo do WhatsApp
(WhatsApp.exe / WhatsApp.Root.exe) está rodando. Se não estiver, reabre
automaticamente via URI 'whatsapp:' — o usuário nunca precisa lembrar de
abrir manualmente.

Uso:
    from core.wa_desktop_watchdog import iniciar_wa_watchdog
    iniciar_wa_watchdog()   # some quando o processo pai morrer
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("wa_desktop_watchdog")

_INTERVALO = 60          # segundos entre checagens
_MAX_FALHAS = 3          # falhas seguidas antes de recuar
_BACKOFF = 300           # 5 min de espera em caso de kill loop
_thread: threading.Thread | None = None
_parar = threading.Event()


def _loop():
    from integrations.whatsapp_desktop import (  # noqa: PLC0415
        _processo_wa_rodando, _abrir_whatsapp_desktop,
    )
    falhas = 0
    log.info("Watchdog do WhatsApp Desktop iniciado (intervalo %ds).", _INTERVALO)
    while not _parar.is_set():
        try:
            if _processo_wa_rodando():
                if falhas:
                    log.info("WhatsApp Desktop voltou (após %d falha[s]).", falhas)
                falhas = 0
            else:
                falhas += 1
                log.warning("WhatsApp Desktop caído (falha %d/%d) — reabrindo…",
                            falhas, _MAX_FALHAS)
                if _abrir_whatsapp_desktop():
                    log.info("WhatsApp Desktop reaberto com sucesso.")
                    falhas = 0
                elif falhas >= _MAX_FALHAS:
                    log.error("Falha ao reabrir %dx — recuando por %ds.",
                              _MAX_FALHAS, _BACKOFF)
                    _parar.wait(_BACKOFF)
                    falhas = 0
                    continue
        except Exception as e:
            log.exception("Watchdog WA: exceção — %s", e)
        _parar.wait(_INTERVALO)


def iniciar_wa_watchdog() -> None:
    """Inicia a thread de watchdog do WhatsApp Desktop (idempotente)."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _parar.clear()
    _thread = threading.Thread(target=_loop, name="wa-desktop-watchdog", daemon=True)
    _thread.start()


def parar_wa_watchdog() -> None:
    _parar.set()
