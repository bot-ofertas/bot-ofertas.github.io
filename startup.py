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


def iniciar_rastreador():
    # O WhatsApp roda em navegador headless próprio (whatsapp_playwright) — não
    # precisa abrir o Chrome do usuário nem atrapalhar o uso do PC.
    cmd = f'cmd /c python "{os.path.join(BASE, "rastreador.py")}" --loop 20 >> "{LOG}" 2>&1'
    proc = subprocess.Popen(cmd, shell=True, cwd=BASE)
    with open(PID, "w") as f:
        f.write(str(proc.pid))
    return proc


if __name__ == "__main__":
    if ja_rodando():
        print("Rastreador já está em execução — não iniciando duplicata.")
    else:
        proc = iniciar_rastreador()
        proc.wait()
