# -*- coding: utf-8 -*-
"""
SITE PUBLISHER — envia as paginas geradas por core/blog_generator.py
(docs/ofertas/*.html, docs/sitemap.xml, docs/robots.txt) pro GitHub Pages.

Por que existe: o GitHub Actions (.github/workflows/bot.yml) so publica
durante a janela em que o PC local fica desligado (01:55-08:45 BRT). O
resto do dia quem gera paginas novas e o bot local (rastreador.py /
rastreador_amazon.py) — sem isso, essas paginas ficam so no disco do
Daniel, sem nenhum trafego chegando (exatamente o bug que motivou criar
este modulo, corrigido em 2026-08-09).

Limite de 1x/hora (2026-08-10): a organizacao "bot-ofertas" foi sinalizada
pelo GitHub por atividade automatizada excessiva (varios pushes/hora entre
o workflow agendado e este publicador local). O workflow em si ja foi
reduzido pra 1x/hora -- este modulo tambem precisa respeitar esse teto,
senao o push local (que roda a cada rodada do bot, ~15-20min) continua
gerando o mesmo padrao suspeito por conta propria.

Best-effort: nunca deve derrubar o rastreador. Qualquer falha (rede,
conflito de git, credencial ausente) e logada e ignorada.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time

log = logging.getLogger("site_publisher")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TIMEOUT = 30
_ESTADO_PATH = os.path.join(_BASE, "data", "site_publisher_last.txt")
_INTERVALO_MIN_S = 3600  # 1x/hora -- mesmo teto do workflow do GitHub Actions


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=_BASE,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        encoding="utf-8",
        errors="replace",
    )


def _pode_publicar_agora() -> bool:
    try:
        with open(_ESTADO_PATH, encoding="utf-8") as f:
            ultimo = float(f.read().strip())
    except (FileNotFoundError, ValueError):
        return True
    return (time.time() - ultimo) >= _INTERVALO_MIN_S


def _marcar_publicado_agora() -> None:
    os.makedirs(os.path.dirname(_ESTADO_PATH), exist_ok=True)
    with open(_ESTADO_PATH, "w", encoding="utf-8") as f:
        f.write(str(time.time()))


def publicar_site(origem: str = "local") -> bool:
    """Commita e publica docs/ (paginas SEO + sitemap) se houver mudanca.

    Limitado a 1x/hora (ver _INTERVALO_MIN_S) para nao somar com o
    workflow agendado do GitHub Actions e gerar um padrao de atividade
    automatizada excessiva.

    Retorna True se publicou algo novo, False se nao havia mudanca, se o
    limite de 1x/hora ainda nao passou, ou se algo falhou (falha nunca
    propaga exception).
    """
    if not _pode_publicar_agora():
        return False

    try:
        add = _git("add", "docs/ofertas/", "docs/sitemap.xml", "docs/robots.txt")
        if add.returncode != 0:
            log.warning("git add falhou: %s", add.stderr.strip()[:300])
            return False

        diff = _git("diff", "--cached", "--quiet")
        if diff.returncode == 0:
            return False  # nada novo pra publicar

        commit = _git("commit", "-m", f"chore: atualiza site ({origem}) [skip ci]")
        if commit.returncode != 0:
            log.warning("git commit falhou: %s", commit.stderr.strip()[:300])
            return False

        pull = _git("pull", "--rebase", "origin", "main")
        if pull.returncode != 0:
            log.warning("git pull --rebase falhou (deixando commit local pra proxima tentativa): %s",
                        pull.stderr.strip()[:300])
            _git("rebase", "--abort")
            return False

        push = _git("push", "origin", "main")
        if push.returncode != 0:
            log.warning("git push falhou (deixando commit local pra proxima tentativa): %s",
                        push.stderr.strip()[:300])
            return False

        _marcar_publicado_agora()
        log.info("Site publicado com sucesso (origem=%s).", origem)
        return True
    except Exception as e:
        log.warning("publicar_site falhou inesperadamente: %s", e)
        return False
