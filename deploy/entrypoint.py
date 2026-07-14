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

import rastreador
rastreador.main()
