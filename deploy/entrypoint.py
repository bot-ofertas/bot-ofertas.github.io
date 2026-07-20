# -*- coding: utf-8 -*-
"""
Entrypoint do container Docker (VPS) — sobe o healthcheck HTTP (:8724)
junto com o rastreador principal, já que aqui não passamos por startup.py.

Uso (via Dockerfile CMD):
    python -u deploy/entrypoint.py --random --loop-min 30 --loop-max 45
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.error_logger import setup_logging
setup_logging()

from core.healthcheck import iniciar_healthcheck
iniciar_healthcheck()

# LOOP_MIN/LOOP_MAX são documentados no docker-compose.vps.yml/.env.example
# como configuráveis, mas rastreador.py só lê --loop-min/--loop-max via
# argparse — sem isso aqui, mudar LOOP_MIN/LOOP_MAX no .env da VPS não tinha
# nenhum efeito real (intervalo ficava travado no valor hardcoded do Dockerfile).
sys.argv = [
    sys.argv[0], "--random",
    "--loop-min", os.getenv("LOOP_MIN", "30"),
    "--loop-max", os.getenv("LOOP_MAX", "45"),
]

import rastreador
rastreador.main()
