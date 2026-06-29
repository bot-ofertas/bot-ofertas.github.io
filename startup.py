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


def abrir_whatsapp_web():
    subprocess.Popen("chrome.exe --new-window https://web.whatsapp.com", shell=True)
    time.sleep(10)  # aguarda WhatsApp Web carregar completamente


def iniciar_rastreador():
    # cmd /c redireciona stdout+stderr para o log
    cmd = f'cmd /c python "{os.path.join(BASE, "rastreador.py")}" --loop 20 >> "{LOG}" 2>&1'
    proc = subprocess.Popen(cmd, shell=True, cwd=BASE)
    with open(PID, "w") as f:
        f.write(str(proc.pid))
    return proc


if __name__ == "__main__":
    abrir_whatsapp_web()
    proc = iniciar_rastreador()
    proc.wait()
