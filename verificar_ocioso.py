# -*- coding: utf-8 -*-
"""
Checagem usada pelo desligamento agendado (aguardar_e_desligar.ps1) — diz se
é seguro desligar o PC agora ou se o bot está no meio de um ciclo de
scraping/postagem.

Uso: python verificar_ocioso.py
Exit code: 0 = ocioso (seguro desligar) | 1 = ocupado (aguarde)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core.database as db

db.inicializar()
if db.execucao_em_andamento():
    print("OCUPADO")
    sys.exit(1)
print("OCIOSO")
sys.exit(0)
