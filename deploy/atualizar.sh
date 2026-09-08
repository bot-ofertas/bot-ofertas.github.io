#!/usr/bin/env bash
# ATUALIZAR — traz o código novo do GitHub para o servidor.
#
#     bash deploy/atualizar.sh
#
# É o lado do servidor da integração com o GitHub: o repositório continua
# sendo a fonte da verdade e o servidor o segue. O caminho é de PUXAR
# (o servidor busca), não de EMPURRAR (o GitHub abrir SSH aqui dentro):
# assim não existe chave de acesso ao servidor guardada no GitHub, nada
# precisa ser aberto no firewall, e trocar o IP do droplet não quebra nada.
#
# Rodando sozinho de hora em hora pelo systemd (deploy/systemd/), é o que
# faz "commitei na branch" virar "está no ar" sem ninguém logar no servidor.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_comum.sh"
exigir_env

LOG="$RAIZ/data/deploy.log"
mkdir -p "$(dirname "$LOG")"
registrar() { echo "$(date '+%Y-%m-%d %H:%M:%S') - $*" | tee -a "$LOG"; }

cd "$RAIZ"

RAMO="$(git rev-parse --abbrev-ref HEAD)"
ANTES="$(git rev-parse HEAD)"

registrar "buscando origin/$RAMO"
if ! git fetch --quiet origin "$RAMO"; then
    registrar "git fetch falhou — mantendo o que já está rodando"
    exit 1
fi

DEPOIS="$(git rev-parse "origin/$RAMO")"
if [[ "$ANTES" == "$DEPOIS" ]]; then
    registrar "já está em $(git rev-parse --short HEAD) — nada a fazer"
    exit 0
fi

# --autostash porque este mesmo checkout recebe as páginas geradas pelo bot
# (docs/, via bind mount) e pode ter mudança não commitada no instante do
# update. Sem ele, o rebase aborta e o servidor congela numa versão antiga
# sem ninguém perceber.
registrar "atualizando $(git rev-parse --short "$ANTES") -> $(git rev-parse --short "$DEPOIS")"
if ! git pull --rebase --autostash --quiet origin "$RAMO"; then
    registrar "git pull --rebase falhou — o bot segue na versão anterior"
    exit 1
fi

# Só reconstrói o que mudou de verdade: `up -d --build` reaproveita as
# camadas do Docker, então uma mudança só em .py não reinstala o Chromium.
registrar "reconstruindo e subindo os containers"
if ! dc up -d --build; then
    registrar "docker compose falhou — CONTAINERS PODEM ESTAR PARADOS"
    exit 1
fi

registrar "atualizado para $(git rev-parse --short HEAD)"
