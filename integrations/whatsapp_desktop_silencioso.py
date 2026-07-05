# -*- coding: utf-8 -*-
"""
WHATSAPP DESKTOP SILENCIOSO — envia mensagens SEM roubar o foco.

Usa pywinauto UIA (UI Automation da Microsoft) que consegue enviar teclas
para controles específicos da janela em segundo plano, mesmo quando ela
está minimizada ou fora do foco.

Fluxo:
  1. Localiza a janela do WhatsApp Desktop (mesmo minimizada)
  2. Conecta via pywinauto backend='uia'
  3. Encontra o campo de busca (Edit) e digita nome do grupo
  4. Encontra o campo de mensagem (Edit no rodapé)
  5. Coloca foto no clipboard e envia via mensagem SendMessage do Windows
  6. Nunca ativa/traz a janela para frente

Vantagem: 100% invisível. Usuário pode estar digitando em outro app que
o bot não interfere.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

log = logging.getLogger("wa_silencioso")


def _copiar_foto_hdrop(caminho: str) -> bool:
    """Copia arquivo (CF_HDROP) para o clipboard."""
    try:
        import struct  # noqa: PLC0415
        import win32clipboard  # noqa: PLC0415
        import win32con  # noqa: PLC0415
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
        log.warning("clipboard foto: %s", e)
        return False


def _copiar_texto(texto: str) -> bool:
    try:
        import win32clipboard  # noqa: PLC0415
        import win32con  # noqa: PLC0415
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, texto)
        win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        log.warning("clipboard texto: %s", e)
        return False


def enviar_silencioso(nome_grupo: str, mensagem: str, caminho_foto: str = "") -> bool:
    """Envia foto+legenda ao grupo SEM ativar a janela do WhatsApp.

    Retorna True se enviou. Se pywinauto não conseguir operar em background,
    retorna False para o chamador tentar o método pyautogui como fallback.
    """
    try:
        from pywinauto import Application  # noqa: PLC0415
        from pywinauto.keyboard import send_keys as _sk  # noqa: PLC0415, F401
    except ImportError:
        log.info("pywinauto não instalado — pip install pywinauto")
        return False

    # Conecta pelo HANDLE da janela detectada pelo pygetwindow (funciona
    # com o WhatsApp UWP, que não expõe processo em modo normal).
    app = None
    win = None
    try:
        import pygetwindow as gw  # noqa: PLC0415
        for w in gw.getAllWindows():
            t = (w.title or "").strip().lower()
            if "whatsapp" in t and "chrome" not in t and "edge" not in t:
                try:
                    hwnd = w._hWnd if hasattr(w, "_hWnd") else None
                    if hwnd:
                        app = Application(backend="uia").connect(handle=hwnd, timeout=5)
                        win = app.window(handle=hwnd)
                        break
                except Exception:
                    continue
    except Exception as e:
        log.debug("pygetwindow: %s", e)

    if app is None:
        # Fallback — tenta pelo processo (funciona em algumas versões)
        for path in ("WhatsApp.exe", "WhatsApp.Root.exe"):
            try:
                app = Application(backend="uia").connect(path=path, timeout=5)
                win = app.top_window()
                break
            except Exception:
                continue

    if app is None or win is None:
        log.info("pywinauto: não conectou ao WhatsApp Desktop")
        return False

    try:
        win.wait("exists", timeout=5)
    except Exception as e:
        log.warning("pywinauto: janela não existe — %s", e)
        return False

    try:
        # 1. Ache a caixa de busca (edit) e digite o nome do grupo
        edits = win.descendants(control_type="Edit")
        if not edits:
            log.info("pywinauto: sem controles Edit visíveis")
            return False
        busca = edits[0]
        # set_edit_text funciona em background (usa mensagens do Windows,
        # não teclas físicas — não afeta o foco atual do usuário)
        busca.set_edit_text(nome_grupo)
        time.sleep(1.2)
        busca.type_keys("{ENTER}", set_foreground=False)
        time.sleep(1.5)

        # 2. Localiza o campo de compose (última Edit visível)
        edits = win.descendants(control_type="Edit")
        compose = edits[-1] if edits else None
        if compose is None:
            return False

        # 3. Foto: coloca no clipboard e cola via Ctrl+V (set_foreground=False)
        if caminho_foto and os.path.exists(caminho_foto):
            if _copiar_foto_hdrop(caminho_foto):
                compose.type_keys("^v", set_foreground=False)
                time.sleep(3.0)
                # Cola legenda
                if _copiar_texto(mensagem):
                    compose.type_keys("^v", set_foreground=False)
                    time.sleep(0.6)
                compose.type_keys("{ENTER}", set_foreground=False)
                time.sleep(1.2)
                log.info("✅ WA silencioso: foto+legenda enviada para '%s'", nome_grupo)
                return True

        # 4. Sem foto: só texto
        if _copiar_texto(mensagem):
            compose.type_keys("^v", set_foreground=False)
            time.sleep(0.4)
        else:
            compose.set_edit_text(mensagem)
        compose.type_keys("{ENTER}", set_foreground=False)
        time.sleep(0.8)
        log.info("✅ WA silencioso: texto enviado para '%s'", nome_grupo)
        return True
    except Exception as e:
        from core.error_logger import log_erro  # noqa: PLC0415
        log_erro("wa_silencioso.envio", e, {"grupo": nome_grupo})
        return False
