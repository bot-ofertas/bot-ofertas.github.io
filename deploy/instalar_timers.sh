#!/usr/bin/env bash
# TIMERS — deixa o servidor se manter sozinho.
#
#     sudo bash deploy/instalar_timers.sh
#
# Instala dois temporizadores do systemd:
#
#   bot-ofertas-atualizar.timer   de hora em hora: puxa o código do GitHub e
#                                 reconstrói se mudou (deploy/atualizar.sh)
#   bot-ofertas-site.timer        de hora em hora, aos 30 min: regenera e
#                                 publica o site (deploy/publicar_site.sh)
#
# Os dois arquivos .service são GERADOS aqui, com o caminho real deste
# repositório, em vez de ficarem versionados com um caminho chutado dentro —
# um `/root/bot_ofertas` escrito à mão no repositório seria um caminho a mais
# para envelhecer errado, e o sintoma seria um timer que roda e não faz nada.
#
# Os dois usam Persistent=true de propósito. Aqui "recuperar um disparo
# perdido" é exatamente o trabalho: se o servidor ficou fora do ar por uma
# hora, atualizar e publicar assim que voltar é o certo. É o oposto das
# tarefas de desligamento do PC (Regra 15), onde recuperar um gatilho
# significaria desligar a máquina fora de hora.

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "$DEPLOY_DIR/.." && pwd)"

if ! command -v systemctl &>/dev/null; then
    echo "ERRO: este servidor não usa systemd — agende à mão (cron) chamando:" >&2
    echo "   $DEPLOY_DIR/atualizar.sh   e   $DEPLOY_DIR/publicar_site.sh" >&2
    exit 1
fi

criar_unidade() {
    local nome="$1" descricao="$2" script="$3" quando="$4"

    cat > "/etc/systemd/system/${nome}.service" <<UNIT
[Unit]
Description=${descricao}
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${RAIZ}
ExecStart=/usr/bin/env bash ${script}
# Sem SuccessExitStatus mascarando saida 1: uma unidade `oneshot` que falha
# NAO impede os disparos seguintes do timer (o proximo roda igual), entao
# tratar erro como sucesso so servia para `systemctl status` mentir dizendo
# "success" enquanto a atualizacao nao acontecia — exatamente o tipo de
# silencio que este projeto ja pagou caro.
UNIT

    cat > "/etc/systemd/system/${nome}.timer" <<UNIT
[Unit]
Description=${descricao} (agendado)

[Timer]
OnCalendar=${quando}
Persistent=true
RandomizedDelaySec=120

[Install]
WantedBy=timers.target
UNIT

    systemctl enable --now "${nome}.timer"
    echo "→ ${nome}.timer instalado (${quando})"
}

criar_unidade bot-ofertas-atualizar \
    "Bot Ofertas — atualiza o codigo a partir do GitHub" \
    "${DEPLOY_DIR}/atualizar.sh" \
    "*-*-* *:00:00"

criar_unidade bot-ofertas-site \
    "Bot Ofertas — publica o site no GitHub Pages" \
    "${DEPLOY_DIR}/publicar_site.sh" \
    "*-*-* *:30:00"

systemctl daemon-reload
echo
systemctl list-timers 'bot-ofertas-*' --no-pager || true
echo
echo "Ver o que aconteceu no último disparo:"
echo "    journalctl -u bot-ofertas-atualizar.service -n 50 --no-pager"
echo "    tail -20 ${RAIZ}/data/deploy.log"
