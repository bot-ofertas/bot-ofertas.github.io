# -*- coding: utf-8 -*-
"""
CHROME MANAGER — garante que o Chrome dedicado do bot esteja SEMPRE pronto.

Responsabilidades:
  - Encontrar o executável do Chrome no Windows
  - Iniciar o Chrome com --remote-debugging-port=9222 e perfil dedicado
  - Verificar disponibilidade da porta 9222 com backoff exponencial
  - Detectar Chrome caído e reiniciar automaticamente
  - Impedir CDP connect antes do Chrome responder /json/version
  - Nunca abrir múltiplas instâncias

Uso:
    from core.chrome_manager import garantir_chrome_pronto, esta_pronto
    if garantir_chrome_pronto(timeout=60):
        # agora pode conectar Playwright/CDP com segurança
        ...
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
import urllib.request

log = logging.getLogger("chrome_manager")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERFIL = os.path.join(_BASE, "data", "chrome_bot")
PORTA_CDP = 9222
CDP_URL = f"http://127.0.0.1:{PORTA_CDP}"

_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Users\{user}\AppData\Local\Google\Chrome\Application\chrome.exe",
]

_CHROME_ARGS = [
    f"--remote-debugging-port={PORTA_CDP}",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-popup-blocking",
    "--disable-dev-shm-usage",
    "--disable-features=Translate,BackgroundTimerThrottling",
    "--window-position=100,100",
    "--window-size=520,760",
    "https://web.whatsapp.com",
]


def achar_chrome_exe() -> str | None:
    """Localiza o executável do Chrome instalado. Retorna caminho ou None."""
    for p in _CHROME_PATHS:
        caminho = p.replace("{user}", os.getenv("USERNAME", ""))
        if os.path.exists(caminho):
            return caminho
    return None


def porta_aberta(host: str = "127.0.0.1", porta: int = PORTA_CDP, timeout: float = 1.0) -> bool:
    """Retorna True se a porta está aceitando conexões TCP."""
    try:
        with socket.create_connection((host, porta), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def cdp_responde(timeout: float = 2.0) -> bool:
    """Confirma que o Chrome responde /json/version — não basta a porta estar aberta."""
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=timeout) as r:
            return r.status == 200 and b"Chrome" in r.read(200)
    except Exception:
        return False


def esta_pronto() -> bool:
    """True se o Chrome do bot está de fato operacional (porta + resposta CDP)."""
    return porta_aberta() and cdp_responde()


def _chrome_do_bot_rodando() -> bool:
    """Detecta se já existe um chrome.exe usando o perfil do bot."""
    try:
        import psutil  # noqa: PLC0415
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                if (p.info.get("name") or "").lower() != "chrome.exe":
                    continue
                cl = " ".join(p.info.get("cmdline") or [])
                if "chrome_bot" in cl and f"remote-debugging-port={PORTA_CDP}" in cl:
                    return True
            except Exception:
                continue
    except ImportError:
        pass
    return False


def _limpar_locks_perfil() -> None:
    """Remove locks residuais de sessão anterior travada."""
    for nome in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.unlink(os.path.join(PERFIL, nome))
        except FileNotFoundError:
            pass
        except OSError:
            pass


def iniciar_chrome() -> subprocess.Popen | None:
    """Inicia o Chrome do bot com perfil dedicado + porta de depuração.

    ATENÇÃO: só inicia se WHATSAPP_CHROME_FALLBACK=1 no .env.
    Por padrão o WhatsApp usa o app nativo (WhatsApp Desktop) — o Chrome do
    bot só é útil como fallback opt-in. Isso evita abrir janelas de QR
    espontaneamente.
    """
    if os.getenv("WHATSAPP_CHROME_FALLBACK", "0") != "1":
        log.debug("Chrome do bot desativado (WHATSAPP_CHROME_FALLBACK != 1).")
        return None

    if _chrome_do_bot_rodando() and porta_aberta():
        log.info("Chrome do bot já está rodando na porta %d — reaproveitando.", PORTA_CDP)
        return None

    chrome = achar_chrome_exe()
    if not chrome:
        log.error("Chrome não encontrado. Instale o Google Chrome.")
        return None

    os.makedirs(PERFIL, exist_ok=True)
    _limpar_locks_perfil()

    cmd = [chrome, f"--user-data-dir={PERFIL}"] + _CHROME_ARGS
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        log.info("Chrome do bot iniciado (PID %d).", proc.pid)
        return proc
    except Exception as e:
        log.error("Falha ao iniciar Chrome do bot: %s", e)
        return None


def aguardar_porta_9222(timeout: float = 45.0) -> bool:
    """Espera a porta 9222 responder /json/version, com backoff exponencial.

    Aguarda tanto o listen quanto o HTTP handshake — evita o clássico
    'ECONNREFUSED' que ocorre se o Playwright conectar cedo demais.
    """
    fim = time.monotonic() + timeout
    espera = 0.5
    while time.monotonic() < fim:
        if cdp_responde():
            log.info("Chrome CDP pronto na porta %d.", PORTA_CDP)
            return True
        time.sleep(espera)
        espera = min(espera * 1.4, 3.0)
    log.warning("Timeout aguardando Chrome CDP na porta %d após %.1fs.", PORTA_CDP, timeout)
    return False


def garantir_chrome_pronto(timeout: float = 60.0) -> bool:
    """API principal — chame antes de qualquer operação CDP/Playwright.

    Se o Chrome já está pronto, retorna True imediatamente. Se não, tenta
    iniciar e aguardar a porta responder. Retorna True apenas quando o CDP
    responde de fato — impede conexão prematura.
    """
    if esta_pronto():
        return True

    log.info("Chrome não está pronto — iniciando…")
    iniciar_chrome()
    if aguardar_porta_9222(timeout=timeout):
        return True

    log.error("Não consegui deixar o Chrome pronto em %.1fs.", timeout)
    return False


def reiniciar_chrome(timeout: float = 60.0) -> bool:
    """Mata o Chrome do bot (se existir) e reinicia. Usado pelo watchdog."""
    try:
        import psutil  # noqa: PLC0415
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                if (p.info.get("name") or "").lower() == "chrome.exe":
                    cl = " ".join(p.info.get("cmdline") or [])
                    if "chrome_bot" in cl:
                        p.kill()
            except Exception:
                continue
    except ImportError:
        pass
    time.sleep(2)
    _limpar_locks_perfil()
    iniciar_chrome()
    return aguardar_porta_9222(timeout=timeout)
