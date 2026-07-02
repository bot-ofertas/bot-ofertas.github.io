# -*- coding: utf-8 -*-
"""
WATCHDOG — monitora saúde do Chrome e reinicia se cair.

Roda em thread separada. NÃO trava o rastreador. Se o Chrome do bot cair
por qualquer motivo (crash, memory pressure, kill manual), o watchdog
detecta em ~30s e reinicia. Se falhar 3 vezes seguidas, para de tentar
por 5 min (backoff) para não entrar em loop de kill.

Uso:
    from core.watchdog import iniciar_watchdog
    iniciar_watchdog()   # dispara thread em daemon; some quando o processo pai morrer
"""
from __future__ import annotations

import logging
import threading
import time

from core.chrome_manager import esta_pronto, reiniciar_chrome

log = logging.getLogger("watchdog")

_INTERVALO = 30           # segundos entre checagens
_MAX_FALHAS = 3           # falhas seguidas antes do backoff longo
_BACKOFF_LONGO = 300      # 5 min em caso de kill loop
_thread: threading.Thread | None = None
_parar = threading.Event()


def _loop():
    falhas_seguidas = 0
    log.info("Watchdog iniciado (intervalo %ds).", _INTERVALO)
    while not _parar.is_set():
        try:
            if esta_pronto():
                if falhas_seguidas:
                    log.info("Chrome voltou ao ar após %d falha(s).", falhas_seguidas)
                falhas_seguidas = 0
            else:
                falhas_seguidas += 1
                log.warning("Chrome do bot caído (falha %d/%d) — tentando reiniciar…",
                            falhas_seguidas, _MAX_FALHAS)
                sucesso = reiniciar_chrome(timeout=45)
                if sucesso:
                    log.info("Chrome do bot reiniciado com sucesso.")
                    falhas_seguidas = 0
                elif falhas_seguidas >= _MAX_FALHAS:
                    log.error("Chrome falhou %d vezes seguidas — recuando por %ds.",
                              _MAX_FALHAS, _BACKOFF_LONGO)
                    _parar.wait(_BACKOFF_LONGO)
                    falhas_seguidas = 0
                    continue
        except Exception as e:
            log.exception("Watchdog: exceção não tratada — %s", e)

        _parar.wait(_INTERVALO)


def iniciar_watchdog() -> None:
    """Inicia a thread de monitoramento (idempotente)."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _parar.clear()
    _thread = threading.Thread(target=_loop, name="chrome-watchdog", daemon=True)
    _thread.start()


def parar_watchdog() -> None:
    _parar.set()
