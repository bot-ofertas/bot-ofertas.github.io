#!/usr/bin/env bash
# CHAVE DE DEPLOY — dá ao servidor permissão de escrever neste repositório,
# sem que nenhuma senha ou token seja digitado em lugar nenhum.
#
#     bash deploy/configurar_git_deploy.sh
#
# Como funciona: o script gera aqui no servidor um par de chaves SSH. A parte
# privada nunca sai deste disco (nem aparece na tela, nem vai para o
# repositório). A parte PÚBLICA é impressa no fim — é ela que você cola no
# GitHub. Uma chave pública não é segredo: sozinha ela não abre nada.
#
# Precisa disto para o servidor conseguir publicar o site (docs/) no GitHub
# Pages. Só para PUXAR código de um repositório público, não é necessário.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_comum.sh"

CHAVE="${HOME:-/root}/.ssh/bot_ofertas_deploy"
mkdir -p "$(dirname "$CHAVE")"; chmod 700 "$(dirname "$CHAVE")"

cd "$RAIZ"
URL="$(git remote get-url origin)"
# Aceita as duas formas que o remoto pode ter e extrai dono/repo.
CAMINHO="$(printf '%s' "$URL" | sed -E 's#^https://github\.com/##; s#^git@github\.com:##; s#\.git$##')"
# Valida o RESULTADO, nao a diferenca entre entrada e saida. A checagem
# anterior (`"$CAMINHO" == "$URL" && "$URL" != *github.com*`) so pegava o caso
# em que o sed nao mudava NADA — bastava a URL terminar em `.git` para ela
# passar batido. Testado: `https://gitlab.com/fulano/projeto.git` era aceito e
# virava o caminho `https://gitlab.com/fulano/projeto`, que configuraria o
# remoto como `github-bot-ofertas:https://gitlab.com/...` e mandaria colar a
# chave numa URL de settings inexistente.
if [[ ! "$CAMINHO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
    echo "ERRO: o remoto 'origin' nao parece ser do GitHub: $URL" >&2
    echo "       (esperava algo como dono/repositorio; obtive: '$CAMINHO')" >&2
    exit 1
fi

if [[ -f "$CHAVE" ]]; then
    echo "→ já existe uma chave em $CHAVE (mantida)."
else
    echo "→ gerando chave de deploy..."
    ssh-keygen -t ed25519 -N "" -C "bot-ofertas-servidor" -f "$CHAVE" >/dev/null
fi
chmod 600 "$CHAVE"

CONFIG="${HOME:-/root}/.ssh/config"
if ! grep -q "Host github-bot-ofertas" "$CONFIG" 2>/dev/null; then
    cat >> "$CONFIG" <<CFG

Host github-bot-ofertas
    HostName github.com
    User git
    IdentityFile $CHAVE
    IdentitiesOnly yes
CFG
    chmod 600 "$CONFIG"
    echo "→ apelido SSH 'github-bot-ofertas' criado em $CONFIG"
fi

git remote set-url origin "github-bot-ofertas:${CAMINHO}.git"
echo "→ remoto origin agora usa a chave de deploy: $(git remote get-url origin)"

# Identidade dos commits do site. Sem isto o `git commit` do
# publicar_site.sh falha com "Please tell me who you are".
git config user.name  "Bot-Ofertas (servidor)"
git config user.email "bot@bot-ofertas.github.io"

cat <<MSG

════════════════════════════════════════════════════════════
  FALTA UM PASSO, E ELE É SEU
════════════════════════════════════════════════════════════

Copie a linha abaixo (é a chave PÚBLICA — pode circular à vontade):

$(cat "${CHAVE}.pub")

E cole em:

    https://github.com/${CAMINHO}/settings/keys/new

    Title: servidor bot-ofertas
    [x] Allow write access      ← marque, senão o site não publica

Depois confirme que funcionou:

    ssh -T github-bot-ofertas
    bash deploy/publicar_site.sh
MSG
