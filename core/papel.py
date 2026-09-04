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
import subprocess
import sys
import time
from datetime import datetime

from core import janela

log = logging.getLogger("papel")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Marcas que SO o publicador do PC deixa no historico do repositorio.
# `core/site_publisher.py` roda a cada rodada do bot local e empurra `docs/`
# com "chore: atualiza site (rastreador-ml)" / "(rastreador-amazon)". O
# GitHub Actions commita "chore: atualiza ofertas do site" (sem origem) e o
# servidor, "(servidor)" — nenhum dos dois casa com estas marcas.
_MARCAS_DO_PC = ("[pc-local]", "atualiza site (rastreador")

# Quanto silencio do PC basta para a nuvem concluir que ele NAO esta
# publicando. 0 desliga a checagem (a nuvem sempre espera dentro da janela).
HORAS_SILENCIO_PADRAO = 6.0

# Quantos commits olhar para tras. Precisa cobrir com folga um dia de
# publicacao do PC (ele empurra no maximo 1x/hora, ~17h de janela).
_COMMITS_INSPECIONADOS = 80

_cache_sinal: tuple[float, "float | None"] | None = None
_CACHE_S = 300

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


def _horas_silencio_max() -> float:
    bruto = (os.getenv("PC_SILENCIO_MAX_H") or "").strip()
    if not bruto:
        return HORAS_SILENCIO_PADRAO
    try:
        return max(0.0, float(bruto))
    except ValueError:
        return HORAS_SILENCIO_PADRAO


def horas_desde_sinal_do_pc(agora: datetime | None = None) -> float | None:
    """Ha quantas horas o PC deu o ultimo sinal de vida, ou None se nao da
    para saber.

    O sinal e o proprio historico do repositorio: o bot no PC empurra `docs/`
    a cada rodada (`core/site_publisher.py`), e essa marca fica visivel para
    qualquer um que tenha um checkout — inclusive o runner do GitHub e o
    servidor. Nao exige rede ate o PC nem porta aberta.

    Devolve None de proposito quando o historico nao alcanca o periodo
    perguntado (checkout raso do Actions, repositorio recem-clonado): "nao
    consegui olhar" nao pode virar "o PC esta morto". Mesmo cuidado que
    `startup.checagem_de_processos_confiavel()` tomou com o psutil — agir
    sobre uma resposta cega foi o que quase duplicou os rastreadores.
    """
    global _cache_sinal
    agora = agora or datetime.now()

    if _cache_sinal is not None and (time.time() - _cache_sinal[0]) < _CACHE_S:
        return _cache_sinal[1]

    try:
        r = subprocess.run(
            # `-- docs/` estreita a janela aos commits que interessam: os de
            # publicacao do site. Sem isso, uma leva de commits de
            # desenvolvimento empurra os commits do PC para fora dos 80
            # inspecionados e a resposta vira um limite inferior fraco.
            ["git", "log", f"-n{_COMMITS_INSPECIONADOS}", "--format=%ct%x09%s",
             "--", "docs/"],
            cwd=_BASE, capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as e:
        # Aviso, nao debug: sem git (ou sem `.git`) esta checagem vira no-op e
        # o publicador de nuvem passa a esperar um PC que pode estar fora do
        # ar. E uma degradacao segura, mas nao pode ser invisivel.
        log.warning(
            "Nao consegui ler o historico para saber do PC (%s) — o papel de "
            "nuvem vai esperar dentro da janela do PC, mesmo que ele esteja "
            "fora do ar. No servidor, confira a montagem de `.git` e o `git` "
            "na imagem (deploy/).", e,
        )
        return None
    if r.returncode != 0 or not r.stdout.strip():
        log.warning("git log nao devolveu historico (%s) — sem sinal do PC.",
                    (r.stderr or "").strip()[:120])
        return None

    limite = _horas_silencio_max()
    agora_ts = agora.timestamp()
    mais_antigo: float | None = None

    resultado: float | None = None
    for linha in r.stdout.splitlines():
        ts_txt, _, assunto = linha.partition("\t")
        try:
            ts = float(ts_txt)
        except ValueError:
            continue
        mais_antigo = ts
        if any(m in assunto for m in _MARCAS_DO_PC):
            resultado = max(0.0, (agora_ts - ts) / 3600.0)
            break

    if resultado is None:
        # Nenhuma marca do PC no trecho olhado. So da para concluir "o PC
        # esta calado" se o historico voltar mais do que o periodo perguntado
        # — senao o que faltou foi historico, nao publicacao.
        if mais_antigo is None or (agora_ts - mais_antigo) / 3600.0 < limite:
            _cache_sinal = (time.time(), None)
            return None
        # Atencao: aqui o numero e um LIMITE INFERIOR ("esta calado ha pelo
        # menos isto"), nao a idade do ultimo sinal — o ultimo sinal do PC
        # pode ser mais recente e simplesmente nao estar no trecho olhado. A
        # decisao (>= limite) continua valida; a redacao de quem mostra o
        # numero e que precisa dizer "pelo menos".
        resultado = (agora_ts - mais_antigo) / 3600.0

    _cache_sinal = (time.time(), resultado)
    return resultado


def pc_parece_morto(agora: datetime | None = None) -> tuple[bool, str]:
    """(o PC esta calado ha tempo demais?, explicacao)."""
    limite = _horas_silencio_max()
    if limite <= 0:
        return False, "checagem de silencio desligada (PC_SILENCIO_MAX_H=0)"
    horas = horas_desde_sinal_do_pc(agora)
    if horas is None:
        return False, "sem historico suficiente para saber do PC"
    if horas >= limite:
        # "pelo menos": ver a nota em horas_desde_sinal_do_pc sobre o caso em
        # que nenhuma marca do PC aparece no trecho inspecionado.
        return True, f"o PC nao publica ha pelo menos {horas:.1f}h (limite {limite:.0f}h)"
    return False, f"o PC publicou ha {horas:.1f}h"


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
        # O relogio dizer que o PC deveria estar ligado nao e o mesmo que ele
        # estar publicando. Em 04/09/2026 o PC estava fora do ar havia 6 dias
        # (ultimo commit dele em 29/08 08:50) e o agendamento da nuvem estava
        # morto havia 5 semanas: os grupos ficaram sem oferta nenhuma, e uma
        # trava que so olha a hora teria mantido a nuvem calada de dia
        # "para nao atrapalhar" um PC que nao existia mais.
        morto, explicacao = pc_parece_morto(agora)
        if morto:
            return True, (
                f"papel nuvem — dentro da janela do PC, mas {explicacao}: "
                "a nuvem assume para os grupos nao ficarem sem oferta"
            )
        return False, (
            "papel nuvem — o PC local pode estar publicando agora "
            f"(janela {janela.hora_ligar():%H:%M}-{janela.hora_desligar():%H:%M} "
            f"+{janela.MINUTOS_ESPERA_DESLIGAR}min de carencia; {explicacao}); "
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
        "horas_sem_sinal_do_pc": horas_desde_sinal_do_pc(agora),
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
