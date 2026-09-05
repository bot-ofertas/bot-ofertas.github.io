#!/usr/bin/env bash
# PUBLICAR SITE — regenera docs/ a partir do banco e publica no GitHub Pages.
#
#     bash deploy/publicar_site.sh
#
# Por que precisa existir no servidor:
#
# O site (bot-ofertas.github.io) sai de `export_json.py`, que lê o banco. No
# PC e no GitHub Actions o banco está ao lado do repositório e isso resolvia
# sozinho. No servidor o banco vive dentro de um volume Docker, e o
# `core/site_publisher.py` que roda DENTRO do container não tem `.git`
# nenhum para commitar (o `.dockerignore` exclui o `.git` de propósito — não
# se põe credencial de push dentro de uma imagem). Sem este script, o dia em
# que o servidor virar o publicador principal é o dia em que o site congela.
#
# Aqui o container só GERA (escreve em /app/docs, que é o docs/ deste
# repositório por bind mount) e o servidor, fora do container, commita e
# empurra — com a chave de deploy configurada por
# `deploy/configurar_git_deploy.sh`.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_comum.sh"
exigir_env

cd "$RAIZ"

echo "→ regenerando docs/data/offers.json a partir do banco..."
dc exec -T rastreador python export_json.py

git add docs/data/offers.json docs/ofertas/ docs/sitemap.xml docs/robots.txt 2>/dev/null || true

if git diff --cached --quiet; then
    echo "→ nada mudou no site."
    exit 0
fi

git commit -q -m "chore: atualiza ofertas do site (servidor) [skip ci]"

# --rebase e não merge: o GitHub Actions e o PC também commitam em docs/, e
# um merge automático aqui encheria o histórico de commits de merge vazios.
if ! git pull --rebase --autostash --quiet origin "$(git rev-parse --abbrev-ref HEAD)"; then
    echo "!! git pull --rebase falhou — o commit fica local e sai na próxima." >&2
    git rebase --abort 2>/dev/null || true
    exit 1
fi

if ! git push --quiet origin HEAD; then
    cat >&2 <<MSG
!! git push falhou. O commit fica local e sai na próxima tentativa.

   Se for a primeira vez, falta configurar a chave de deploy:
       bash deploy/configurar_git_deploy.sh
MSG
    exit 1
fi

echo "→ site publicado."
