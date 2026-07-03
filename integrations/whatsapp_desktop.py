# -*- coding: utf-8 -*-
"""
ENVIO WHATSAPP via WhatsApp Desktop (aplicativo nativo Windows)
================================================================
Controla o app nativo do WhatsApp (WhatsApp.Root.exe) diretamente.
Não sofre limitações de navegador (headless, CDP, MCP block).

Estratégia:
  1. Foca janela do WhatsApp Desktop
  2. Ctrl+F → busca conversa "Bot-Ofertas" → Enter (abre)
  3. Copia foto (CF_HDROP) e Ctrl+V → abre preview
  4. Digita legenda completa no preview
  5. Enter para enviar
  6. Devolve foco para janela anterior (não atrapalha o usuário)

Pré-requisito: WhatsApp Desktop instalado e logado no Windows.
Baixe em: https://www.microsoft.com/store/apps/9NKSQGP7F2NH
"""
from __future__ import annotations

import io
import logging
import os
import struct
import time
from typing import Optional

log = logging.getLogger("whatsapp_desktop")


def _janela_whatsapp():
    """Retorna a janela do WhatsApp Desktop APENAS se estiver LOGADO.

    Filtra:
      - Abas do Chrome/Edge com título 'WhatsApp' (não é o app nativo)
      - Janelas mostrando QR de login (WhatsApp desvinculado)

    Detecção de login: se o processo WhatsApp.Root.exe está rodando E existe
    janela com título 'WhatsApp' pura, provavelmente está logado. Se o próprio
    setup_whatsapp_cdp abriu uma janela do Chrome do bot mostrando QR, ela vai
    conter '- Google Chrome' no título e é filtrada.
    """
    try:
        import pygetwindow as gw  # noqa: PLC0415
    except ImportError:
        return None

    # Só considera candidato se o processo NATIVO estiver rodando
    processo_ok = False
    try:
        import psutil  # noqa: PLC0415
        for p in psutil.process_iter(["name"]):
            n = (p.info.get("name") or "").lower()
            if n in ("whatsapp.exe", "whatsapp.root.exe"):
                processo_ok = True
                break
    except ImportError:
        processo_ok = True  # sem psutil, tenta mesmo assim

    if not processo_ok:
        return None

    for w in gw.getAllWindows():
        t = (w.title or "").strip()
        if not t:
            continue
        low = t.lower()
        # Título nativo: "WhatsApp" ou "(N) WhatsApp".
        # Exclui abas de navegador (têm "chrome" ou "edge" no título).
        if "whatsapp" in low and "chrome" not in low and "edge" not in low:
            return w
    return None


def _copiar_foto_clipboard(caminho: str) -> bool:
    """Copia o arquivo de foto para o clipboard como CF_HDROP."""
    if not caminho or not os.path.exists(caminho):
        return False
    try:
        import win32clipboard  # noqa: PLC0415
        import win32con        # noqa: PLC0415
        offset = 20
        lista = (caminho + "\0\0").encode("utf-16-le")
        dropfiles = struct.pack("<LllII", offset, 0, 0, 0, 1)
        buf = dropfiles + lista
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_HDROP, buf)
        win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        log.warning("clipboard: %s", e)
        return False


def _copiar_texto_clipboard(texto: str) -> bool:
    """Copia texto ao clipboard (para colar legenda de uma vez)."""
    try:
        import win32clipboard  # noqa: PLC0415
        import win32con        # noqa: PLC0415
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, texto)
        win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        log.warning("clipboard texto: %s", e)
        return False


def _baixar_e_salvar_foto(url: str) -> Optional[str]:
    """Baixa a foto do produto e salva otimizada para envio."""
    if not url or not url.startswith("http"):
        return None
    try:
        import requests  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        destino = os.path.join(base, "data", f"wa_desktop_{int(time.time() * 1000)}.jpg")
        r = requests.get(url, timeout=10)
        if r.status_code != 200 or not r.content:
            return None
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        img.thumbnail((900, 900))
        img.save(destino, "JPEG", quality=85, optimize=True)
        return destino
    except Exception as e:
        log.warning("baixar foto: %s", e)
        return None


def _limpar_fotos_antigas() -> None:
    try:
        import glob  # noqa: PLC0415
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        agora = time.time()
        for f in glob.glob(os.path.join(base, "data", "wa_desktop_*.jpg")):
            try:
                if agora - os.path.getmtime(f) > 300:
                    os.remove(f)
            except OSError:
                pass
    except Exception:
        pass


def _ativar_janela(janela) -> bool:
    """Ativa a janela do WhatsApp Desktop, tolerando falso-erro do pygetwindow."""
    for _ in (1, 2):
        try:
            janela.activate()
            return True
        except Exception:
            try:
                if janela.isMinimized:
                    janela.restore()
                janela.maximize()
                return True
            except Exception:
                pass
            time.sleep(0.4)
    return False


def _processo_wa_rodando() -> bool:
    """True se o processo nativo do WhatsApp está rodando."""
    try:
        import psutil  # noqa: PLC0415
        for p in psutil.process_iter(["name"]):
            n = (p.info.get("name") or "").lower()
            if n in ("whatsapp.exe", "whatsapp.root.exe"):
                return True
    except Exception:
        pass
    return False


def _abrir_whatsapp_desktop() -> bool:
    """Abre o app WhatsApp Desktop via URI protocol (whatsapp:).

    O URI whatsapp:// é registrado pelo próprio app quando instalado (via
    Microsoft Store). Funciona em Windows 10/11.
    """
    try:
        import subprocess  # noqa: PLC0415
        # explorer.exe abre URIs registrados; não bloqueia se falhar
        subprocess.Popen(
            ["explorer.exe", "whatsapp:"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Aguarda janela aparecer (até 15s)
        for _ in range(15):
            time.sleep(1)
            if _processo_wa_rodando():
                time.sleep(2)  # deixa carregar a UI
                return True
        return False
    except Exception as e:
        log.warning("Não consegui abrir WhatsApp Desktop: %s", e)
        return False


def garantir_whatsapp_aberto() -> bool:
    """Garante que o WhatsApp Desktop está aberto e detectável.

    Se o processo não está rodando, tenta abrir automaticamente via whatsapp:
    URI. Retorna True se está pronto para uso.
    """
    if _janela_whatsapp() is not None:
        return True
    if _processo_wa_rodando():
        # Processo rodando mas sem janela — aguarda janela aparecer
        for _ in range(8):
            time.sleep(1)
            if _janela_whatsapp() is not None:
                return True
    log.info("WhatsApp Desktop não detectado — tentando abrir…")
    if _abrir_whatsapp_desktop():
        return _janela_whatsapp() is not None
    return False


def enviar_para_grupo_desktop(nome_grupo: str, mensagem: str, foto_url: str = "") -> bool:
    """Envia foto+legenda ao grupo via WhatsApp Desktop (app nativo).

    Retorna True se enviou. Ativa a janela por ~5s, envia e devolve o foco.
    """
    try:
        import pyautogui  # noqa: PLC0415
    except ImportError:
        log.warning("pyautogui não instalado — pip install pyautogui")
        return False

    # Garante que WhatsApp Desktop está aberto (reabre se fechado)
    if not garantir_whatsapp_aberto():
        log.warning("WhatsApp Desktop não pôde ser aberto — pulando envio.")
        return False
    janela = _janela_whatsapp()
    if not janela:
        log.warning("WhatsApp Desktop sem janela após abrir — pulando.")
        return False

    # Baixa foto antes de ativar a janela (mais rápido depois)
    caminho_foto = _baixar_e_salvar_foto(foto_url) if foto_url else ""
    tem_foto = bool(caminho_foto and os.path.exists(caminho_foto))

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.25

    # Salva janela ativa atual para restaurar no fim (não rouba foco permanentemente)
    try:
        import pygetwindow as gw  # noqa: PLC0415
        janela_anterior = gw.getActiveWindow()
    except Exception:
        janela_anterior = None

    try:
        # 1. Ativa WhatsApp Desktop
        if not _ativar_janela(janela):
            log.warning("Não consegui ativar a janela do WhatsApp Desktop.")
            return False
        time.sleep(0.8)

        # 2. Ctrl+F → busca conversa
        pyautogui.hotkey("ctrl", "f")
        time.sleep(0.6)
        pyautogui.typewrite("", interval=0)  # limpa
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        pyautogui.typewrite(nome_grupo, interval=0.04)
        time.sleep(1.2)
        pyautogui.press("enter")
        time.sleep(1.5)

        # 3. Coloca foto no clipboard + Ctrl+V → abre preview
        if tem_foto and _copiar_foto_clipboard(caminho_foto):
            pyautogui.hotkey("ctrl", "v")
            time.sleep(3.0)  # aguarda preview montar

            # 4. Cola legenda (copiada como texto)
            if _copiar_texto_clipboard(mensagem):
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.5)
            else:
                pyautogui.typewrite(mensagem, interval=0.005)
                time.sleep(0.5)

            # 5. Enter para enviar
            pyautogui.press("enter")
            time.sleep(1.5)
            _limpar_fotos_antigas()
            log.info("✅ WhatsApp Desktop enviado COM FOTO para '%s'", nome_grupo)
            resultado = True
        else:
            # Sem foto: envia só texto
            if _copiar_texto_clipboard(mensagem):
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.5)
            else:
                pyautogui.typewrite(mensagem, interval=0.005)
                time.sleep(0.5)
            pyautogui.press("enter")
            time.sleep(0.8)
            log.info("✅ WhatsApp Desktop enviado (texto) para '%s'", nome_grupo)
            resultado = True

        # 6. Devolve foco (minimiza WhatsApp)
        try:
            janela.minimize()
        except Exception:
            pass
        try:
            if janela_anterior:
                janela_anterior.activate()
        except Exception:
            pass

        return resultado
    except Exception as e:
        log.warning("pyautogui WhatsApp Desktop falhou: %s", e)
        try:
            janela.minimize()
        except Exception:
            pass
        return False
