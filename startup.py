# -*- coding: utf-8 -*-
"""
Script de inicialização automática do Bot Ofertas.
Registrado como tarefa do Windows - roda ao fazer login.
"""
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable


def abrir_whatsapp_web():
    """Abre Chrome com WhatsApp Web em segundo plano."""
    subprocess.Popen(
        ["chrome.exe", "--new-window", "https://web.whatsapp.com"],
        shell=True,
    )
    time.sleep(8)


def iniciar_rastreador():
    """Inicia rastreador em loop de 20 minutos."""
    log_path = os.path.join(BASE, "data", "rastreador_local.log")
    with open(log_path, "a", encoding="utf-8") as log_f:
        proc = subprocess.Popen(
            [PYTHON, os.path.join(BASE, "rastreador.py"), "--loop", "20"],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            cwd=BASE,
        )
    pid_path = os.path.join(BASE, "data", "rastreador.pid")
    with open(pid_path, "w") as f:
        f.write(str(proc.pid))
    return proc


if __name__ == "__main__":
    abrir_whatsapp_web()
    proc = iniciar_rastreador()
    proc.wait()
