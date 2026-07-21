# -*- coding: utf-8 -*-
"""
Controla horário/frequência de postagem para Instagram/Twitter/Facebook — as
únicas redes com algoritmo de distribuição, onde postar demais ou rápido
demais faz os próprios posts competirem entre si (canibalização) em vez de
somar alcance. Baseado em pesquisa 2025/2026 sobre os algoritmos de cada
rede para público brasileiro de ofertas/cupons.

Telegram e WhatsApp NÃO passam por aqui — continuam saindo em tempo real,
sempre, como já é regra do projeto (freshness é o produto).

O estado (último post, contagem do dia) fica no SQLite compartilhado
(core/database.py), não em memória — o bot roda como dois processos do SO
separados (rastreador.py e rastreador_amazon.py) que não compartilham
memória, então um dict em módulo veria só metade dos posts reais.
"""
from __future__ import annotations

import datetime

import core.database as db

# Janela "morta" universal (hora local do sistema — assume-se já em horário
# de Brasília, mesma premissa usada no resto do projeto, ex.
# programar.ps1/agendar_shutdown.ps1) — nenhuma fonte pesquisada registra
# atividade relevante em nenhuma plataforma nesse intervalo.
_HORA_MORTA = (0, 6)  # 00:00–06:00

# Espaçamento mínimo entre posts na MESMA plataforma, em minutos — o achado
# mais consistente da pesquisa: postar rápido demais faz um post "roubar" a
# distribuição do anterior em vez de somar alcance.
_ESPACAMENTO_MIN = {
    "instagram_feed": 180,
    "instagram_story": 60,
    "twitter": 20,
    "facebook": 180,
}

# Teto de posts/dia — só para os formatos "feed" (Instagram post, Facebook
# Page), onde a pesquisa converge fortemente em 1-3/dia como ideal. Twitter
# e Stories toleram volume bem mais alto (sem teto aqui).
_TETO_DIARIO = {
    "instagram_feed": 2,
    "facebook": 3,
}


def _em_hora_morta(agora: datetime.datetime) -> bool:
    return _HORA_MORTA[0] <= agora.hour < _HORA_MORTA[1]


def pode_postar(plataforma: str) -> bool:
    """True se agora é um bom momento para publicar em `plataforma`."""
    agora = datetime.datetime.now()

    if _em_hora_morta(agora):
        return False

    espacamento = _ESPACAMENTO_MIN.get(plataforma)
    if espacamento:
        ultimo_iso = db.ultimo_post_social(plataforma)
        if ultimo_iso:
            ultimo = datetime.datetime.fromisoformat(ultimo_iso)
            if (agora - ultimo) < datetime.timedelta(minutes=espacamento):
                return False

    teto = _TETO_DIARIO.get(plataforma)
    if teto:
        inicio_hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        if db.contar_posts_social_desde(plataforma, inicio_hoje) >= teto:
            return False

    return True


def registrar_post(plataforma: str) -> None:
    """Chame depois de um post bem-sucedido, pro gate saber quando foi o último."""
    db.registrar_post_social(plataforma)
