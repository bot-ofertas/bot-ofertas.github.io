# -*- coding: utf-8 -*-
"""
PAUSA — chave de "parar de publicar agora", compartilhada entre processos.

Os 4 processos do bot (ML, Amazon, campanha, fila do WhatsApp) rodam como
subprocessos independentes (ver `startup.py`). Uma variável em memória não
serve para pausar todos: cada um tem a sua. Um arquivo-bandeira em `data/`
é lido por todos, sobrevive a reinício de processo e pode ser criado à mão
(ou pelo n8n via `POST /n8n/comando`) sem derrubar nada.

Pausar é diferente de desligar: os processos continuam vivos, o healthcheck
continua respondendo e a fila continua enchendo — só a publicação para. É o
que se quer quando algo está saindo errado no canal e não se quer perder o
supervisor e o histórico junto.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAG_PATH = os.path.join(_BASE, "data", "pausado.flag")


def pausar(motivo: str = "", origem: str = "manual") -> dict:
    """Cria a bandeira de pausa. Idempotente."""
    info = {
        "pausado_em": datetime.now().isoformat(timespec="seconds"),
        "motivo": motivo or "sem motivo informado",
        "origem": origem,
    }
    os.makedirs(os.path.dirname(FLAG_PATH), exist_ok=True)
    with open(FLAG_PATH, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)
    return info


def retomar() -> bool:
    """Remove a bandeira. True se havia uma pausa ativa."""
    try:
        os.remove(FLAG_PATH)
        return True
    except FileNotFoundError:
        return False


def pausado() -> bool:
    return os.path.exists(FLAG_PATH)


def info() -> dict:
    """Detalhes da pausa ativa ({} quando não há pausa)."""
    if not pausado():
        return {}
    try:
        with open(FLAG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # Arquivo existe mas ilegível: a pausa continua valendo — o estado
        # que importa é a existência da bandeira, não o conteúdo dela.
        return {"pausado_em": "?", "motivo": "arquivo de pausa ilegível"}
