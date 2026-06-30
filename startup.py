# -*- coding: utf-8 -*-
"""
Script de inicialização automática do Bot Ofertas.
Registrado como tarefa do Windows (BotOfertas-AutoStart) - roda ao fazer login.
"""
import os
import subprocess
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LOG  = os.path.join(BASE, "data", "rastreador_local.log")
PID  = os.path.join(BASE, "data", "rastreador.pid")


def ja_rodando() -> bool:
    """Evita duplicatas: True se já existe um rastreador.py em execução."""
    try:
        import psutil  # noqa: PLC0415
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
                if "rastreador.py" in cmd and "python" in (p.info.get("name") or "").lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass
    return False


def _achar_chrome() -> str:
    for c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(c):
            return c
    return "chrome.exe"


def abrir_chrome_bot():
    """Abre o Chrome dedicado do bot (perfil próprio + porta de depuração 9222).

    Janela própria, separada do Chrome do usuário. Fica logada no WhatsApp Web e
    é controlada em segundo plano via CDP. --window-position joga para um canto.
    """
    perfil = os.path.join(BASE, "data", "chrome_bot")
    chrome = _achar_chrome()
    subprocess.Popen([
        chrome,
        f"--user-data-dir={perfil}",
        "--remote-debugging-port=9222",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-position=2000,2000",
        "--window-size=420,640",
        "https://web.whatsapp.com",
    ])
    time.sleep(12)


def iniciar_rastreador():
    cmd = f'cmd /c python "{os.path.join(BASE, "rastreador.py")}" --loop 20 >> "{LOG}" 2>&1'
    proc = subprocess.Popen(cmd, shell=True, cwd=BASE)
    with open(PID, "w") as f:
        f.write(str(proc.pid))
    return proc


if __name__ == "__main__":
    if ja_rodando():
        print("Rastreador já está em execução — não iniciando duplicata.")
    else:
        abrir_chrome_bot()
        proc = iniciar_rastreador()
        proc.wait()
