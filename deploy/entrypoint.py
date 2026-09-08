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

# Diz na primeira linha do log quem este container e e se ele pode publicar
# agora. Um servidor em papel `nuvem` fica quieto o dia inteiro DE PROPOSITO
# (o PC esta ligado); sem esta linha, "quieto por decisao" e "quieto porque
# quebrou" tem exatamente a mesma aparencia nos logs.
import logging  # noqa: E402

from core import papel  # noqa: E402

_estado = papel.resumo()
logging.getLogger("entrypoint").info(
    "Papel=%s | pode publicar agora: %s (%s) | fuso %s%s",
    _estado["papel"], _estado["pode_publicar"], _estado["motivo"],
    _estado["utc_offset"],
    "" if _estado["fuso_ok"] else "  <-- FORA DO HORARIO DE BRASILIA: confira TZ no .env",
)

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
