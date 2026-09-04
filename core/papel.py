# -*- coding: utf-8 -*-
"""
PAPEL — quem, dos publicadores, pode publicar agora.

Por que existe
--------------
Até aqui existia UM publicador (o PC do Daniel, das 08:30 às 02:00) e um
suplente (o GitHub Actions, que só roda enquanto o PC dorme — o passo
"Confirmar que o PC local está dormindo" em `.github/workflows/bot.yml`).
Essa regra vivia escrita como uma linha de shell dentro do workflow.

Com o bot também num servidor (DigitalOcean, `deploy/`), passam a ser TRÊS
processos capazes de postar no mesmo canal do Telegram e no mesmo grupo do
WhatsApp — e cada um com o seu próprio banco de deduplicação: o PC tem
`data/bot_ofertas.db` no disco dele, o Actions tem um cache do runner, o
servidor tem um volume Docker. Nenhum deles enxerga o que o outro já
publicou. Dois rodando ao mesmo tempo não é "um pouco mais de oferta": é a
MESMA oferta saindo duas vezes no grupo, que foi exatamente o estrago de
2026-08-04 (~26 reenvios duplicados) que a Regra 11 existe para evitar.

Então a pergunta "posso publicar agora?" vira uma coisa só, num módulo só,
igual `core/janela.py` fez com os horários — e cada instância só precisa
dizer QUEM ELA É, via a variável de ambiente `PAPEL`.

Papéis
------
`local` (padrão)
    O PC. Não tem trava nenhuma: quem decide quando ele publica é o próprio
    ciclo de ligar/desligar. É o padrão porque o `.env` do PC não define
    `PAPEL` — e não definir nada tem que continuar funcionando exatamente
    como antes (Regra 1).

`nuvem`
    Publicador de plantão: só publica enquanto o PC local NÃO pode estar
    publicando (`core.janela.pc_pode_estar_publicando()`, que já inclui a
    carência de 35 min do desligamento). É o papel do GitHub Actions hoje e
    o padrão seguro para o servidor: sobe, e no primeiro dia já não pisa no
    PC.

`nuvem-exclusiva`
    O servidor é o ÚNICO publicador — publica 24h. Só faz sentido depois de
    calar os outros dois: pausar o PC (`core.pausa`, ou simplesmente não
    subir o bot nele) e pôr o Actions em `desligado`. Se sobrar mais de um
    publicando sem trava, volta o post duplicado.

`desligado`
    Nunca publica. É como se aposenta o GitHub Actions sem apagar o
    workflow: basta trocar a variável `PAPEL` do repositório.

Uso na linha de comando (é como o workflow do GitHub pergunta):

    python -m core.papel --pode-publicar   # exit 0 = pode; 1 = não pode
    python -m core.papel                   # JSON com o estado
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime

from core import janela

log = logging.getLogger("papel")

LOCAL = "local"
NUVEM = "nuvem"
NUVEM_EXCLUSIVA = "nuvem-exclusiva"
DESLIGADO = "desligado"

PAPEIS = (LOCAL, NUVEM, NUVEM_EXCLUSIVA, DESLIGADO)

# Fuso em que os horários do ciclo foram acordados. O servidor precisa estar
# nele — ver `_fuso_conferido()`.
OFFSET_ESPERADO_H = -3


def papel() -> str:
    """O papel desta instância, lido de `PAPEL` no ambiente.

    Duas leituras de valor errado, de propósito diferentes:

    - **Vazio** vira `local`. Quem não define nada é o PC do Daniel, cujo
      `.env` nunca ouviu falar desta variável. Um módulo novo não pode
      calar o publicador que já funcionava.
    - **Escrito errado** (`nuven`, `NUVEM `, `cloud`) vira `nuvem`. Uma
      grafia errada só aparece onde alguém TENTOU configurar um papel — ou
      seja, numa instância de nuvem. Cair em `local` ali seria liberar
      publicação 24h por causa de um erro de digitação, justamente o caso
      que este módulo existe para impedir. Cair em `nuvem` erra para o lado
      de publicar de menos, que custa uma rodada em vez do grupo inteiro
      vendo a oferta repetida.
    """
    bruto = (os.getenv("PAPEL") or "").strip().lower()
    if not bruto:
        return LOCAL
    if bruto in PAPEIS:
        return bruto
    log.warning(
        "PAPEL=%r nao e um papel valido (%s) — assumindo %r, o conservador.",
        bruto, ", ".join(PAPEIS), NUVEM,
    )
    return NUVEM


def _fuso_conferido(agora: datetime | None = None) -> bool:
    """True se o relógio desta máquina está no fuso do ciclo (BRT, UTC-3).

    `core.janela` compara com `datetime.now()`, o relógio local. Num droplet
    recém-criado esse relógio é UTC: a janela 08:30–02:00 BRT vira
    08:30–02:00 UTC, e o papel `nuvem` passa a se achar livre das 23:00 às
    05:30 BRT — três horas publicando por cima do PC ligado, sem erro nenhum
    no log. Por isso o `docker-compose.vps.yml` define
    `TZ: America/Sao_Paulo`, e por isso isto aqui confere.
    """
    agora = agora or datetime.now()
    desloc = agora.astimezone().utcoffset()
    if desloc is None:
        return False
    return desloc.total_seconds() == OFFSET_ESPERADO_H * 3600


def pode_publicar(agora: datetime | None = None) -> tuple[bool, str]:
    """(pode?, motivo). O motivo vai para o log e para `/health` — um
    publicador em silêncio tem que saber dizer por quê."""
    agora = agora or datetime.now()
    p = papel()

    if p == LOCAL:
        return True, "papel local — sem trava (quem decide e o ciclo do PC)"
    if p == DESLIGADO:
        return False, "papel desligado — esta instancia nao publica"
    if p == NUVEM_EXCLUSIVA:
        return True, "papel nuvem-exclusiva — unico publicador, publica 24h"

    # NUVEM: só quando o PC local não pode estar publicando.
    if janela.pc_pode_estar_publicando(agora):
        return False, (
            "papel nuvem — o PC local pode estar publicando agora "
            f"(janela {janela.hora_ligar():%H:%M}-{janela.hora_desligar():%H:%M} "
            f"+{janela.MINUTOS_ESPERA_DESLIGAR}min de carencia); "
            "publicar junto duplicaria a oferta no grupo"
        )
    return True, "papel nuvem — PC local fora da janela, a nuvem cobre"


def bloqueado(agora: datetime | None = None) -> tuple[bool, str]:
    """Inverso de `pode_publicar`, no formato que os rastreadores usam."""
    pode, motivo = pode_publicar(agora)
    return (not pode), motivo


def resumo(agora: datetime | None = None) -> dict:
    """Estado para `/health` e para o relatório diário."""
    agora = agora or datetime.now()
    pode, motivo = pode_publicar(agora)
    return {
        "papel": papel(),
        "pode_publicar": pode,
        "motivo": motivo,
        "fuso_ok": _fuso_conferido(agora),
        "utc_offset": agora.astimezone().strftime("%z"),
    }


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        )
    except Exception:
        pass

    estado = resumo()
    if "--pode-publicar" in sys.argv:
        print(json.dumps(estado, ensure_ascii=False))
        sys.exit(0 if estado["pode_publicar"] else 1)
    if "--papel" in sys.argv:
        print(estado["papel"])
        sys.exit(0)
    print(json.dumps(estado, ensure_ascii=False, indent=2))
