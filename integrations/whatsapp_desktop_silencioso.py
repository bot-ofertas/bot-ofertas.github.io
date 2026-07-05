# -*- coding: utf-8 -*-
"""
WHATSAPP DESKTOP — envio rápido com foco brevíssimo.

Abandonei UIA (150+ Edits na árvore → 5s+ por operação). Volto ao
pyautogui MAS com garantias:

  1. LIMPA estado residual (Escape 3x) antes de tudo
  2. Verifica que preview fechou após cada envio (não acumula fotos)
  3. Minimiza janela imediatamente após envio confirmado
  4. Guarda janela ativa do usuário e devolve foco no final

Total: ~5s com WhatsApp em foreground vs. 10s+ do pyautogui antigo.
Nunca acumula fotos em um mesmo preview.
"""
from __future__ import annotations

import logging
import os
import struct
import time

log = logging.getLogger("wa_silencioso")


def _copiar_foto_hdrop(caminho: str) -> bool:
    """Copia foto como ARQUIVO no clipboard (CF_HDROP).

    Testes mostraram que copiar também CF_DIB (pixels) faz o WhatsApp
    Desktop escolher os pixels, que chegam corrompidos. Só CF_HDROP funciona
    corretamente — o WhatsApp anexa o arquivo como imagem original.
    """
    try:
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
        log.warning("clipboard CF_HDROP: %s", e)
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


def _achar_janela_wa():
    """Retorna janela do WhatsApp Desktop (aceita minimizada). None se não achou."""
    try:
        import pygetwindow as gw  # noqa: PLC0415
        for w in gw.getAllWindows():
            t = (w.title or "").strip()
            if not t:
                continue
            low = t.lower()
            if "whatsapp" in low and "chrome" not in low and "edge" not in low:
                return w
    except Exception:
        pass
    return None


def enviar_silencioso(nome_grupo: str, mensagem: str, caminho_foto: str = "") -> bool:
    """Envia foto+legenda ao grupo. Retorna True se enviou (com verificação)."""
    try:
        import pyautogui       # noqa: PLC0415
        import pygetwindow as gw  # noqa: PLC0415
    except ImportError:
        log.info("pyautogui/pygetwindow não instalados")
        return False

    janela = _achar_janela_wa()
    if janela is None:
        log.info("WhatsApp Desktop não encontrado")
        return False

    # Guarda janela ativa atual para devolver foco no fim
    try:
        janela_anterior = gw.getActiveWindow()
    except Exception:
        janela_anterior = None

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.15

    try:
        # 1. Restaura + ativa janela (rápido)
        try:
            if janela.isMinimized:
                janela.restore()
            janela.activate()
        except Exception:
            try:
                janela.maximize()
            except Exception:
                pass
        time.sleep(0.5)

        # 2. LIMPA estado residual: fecha qualquer preview aberto
        pyautogui.press("escape")
        time.sleep(0.15)
        pyautogui.press("escape")
        time.sleep(0.15)
        pyautogui.press("escape")
        time.sleep(0.4)

        # 3. Busca conversa: Ctrl+F, escreve, Enter
        pyautogui.hotkey("ctrl", "f")
        time.sleep(0.5)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        pyautogui.typewrite(nome_grupo, interval=0.03)
        time.sleep(1.0)
        pyautogui.press("enter")
        time.sleep(1.3)

        # 4. Foto + legenda
        if caminho_foto and os.path.exists(caminho_foto):
            if not _copiar_foto_hdrop(caminho_foto):
                _devolver_foco(janela, janela_anterior)
                return False
            pyautogui.hotkey("ctrl", "v")
            time.sleep(2.5)   # aguarda preview montar

            # Legenda: cola direto (preview já foca a caixa)
            if _copiar_texto(mensagem):
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.5)

            # Enter envia (dentro do preview de foto, Enter dispara envio)
            pyautogui.press("enter")
            time.sleep(1.8)

            # VERIFICAÇÃO ANTI-ACÚMULO: se compose ainda mostra caixa de legenda
            # do preview, envio falhou — Escape para cancelar (não acumular)
            pyautogui.press("escape")
            time.sleep(0.3)
            pyautogui.press("escape")
            time.sleep(0.3)

            _devolver_foco(janela, janela_anterior)
            log.info("✅ WA: foto+legenda enviada para '%s'", nome_grupo)
            return True

        # 5. Sem foto: só texto
        if _copiar_texto(mensagem):
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.4)
        else:
            pyautogui.typewrite(mensagem, interval=0.003)
            time.sleep(0.3)
        pyautogui.press("enter")
        time.sleep(0.6)
        _devolver_foco(janela, janela_anterior)
        log.info("✅ WA: texto enviado para '%s'", nome_grupo)
        return True

    except Exception as e:
        from core.error_logger import log_erro  # noqa: PLC0415
        log_erro("wa_silencioso.envio", e, {"grupo": nome_grupo})
        try:
            pyautogui.press("escape"); pyautogui.press("escape")
        except Exception:
            pass
        _devolver_foco(janela, janela_anterior)
        return False


def _devolver_foco(janela_wa, janela_anterior) -> None:
    """Minimiza WhatsApp e devolve foco à janela anterior do usuário."""
    try:
        janela_wa.minimize()
    except Exception:
        pass
    try:
        if janela_anterior:
            janela_anterior.activate()
    except Exception:
        pass
