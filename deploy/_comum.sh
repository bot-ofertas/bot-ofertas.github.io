# Trechos compartilhados por deploy_vps.sh e botctl.sh. Não executa nada
# sozinho — é feito para `source`.
#
# Existe por causa de dois erros que os scripts anteriores cometiam:
#
# 1. Caminho relativo ao diretório de quem chamou. O `deploy_vps.sh` antigo
#    testava `../.env` ANTES de fazer `cd` para a própria pasta — rodado da
#    raiz do repositório, como o README mandava, ele procurava o `.env` no
#    diretório PAI do repositório e abortava dizendo que faltava configurar.
#    Aqui tudo é resolvido a partir do caminho do próprio arquivo.
#
# 2. Ler valor do `.env` com `cut -d= -f2`. Uma chave gerada por
#    `secrets.token_urlsafe` pode conter `=`, e o `cut` devolvia só o pedaço
#    antes dele: o script criava a instância com uma chave truncada e a
#    Evolution respondia 401 sem que nada no output explicasse por quê.

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "$DEPLOY_DIR/.." && pwd)"
ENV_FILE="$RAIZ/.env"
COMPOSE="$DEPLOY_DIR/docker-compose.vps.yml"

dc() {
    docker compose -f "$COMPOSE" --env-file "$ENV_FILE" "$@"
}

exigir_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        cat >&2 <<MSG
ERRO: não achei $ENV_FILE

    cd "$RAIZ"
    cat .env.example deploy/.env.example > .env
    nano .env
MSG
        exit 1
    fi
}

# Lê uma chave do .env preservando o valor inteiro (inclusive '=' e aspas).
ler_env() {
    local chave="$1"
    sed -n "s/^[[:space:]]*${chave}[[:space:]]*=//p" "$ENV_FILE" \
        | tail -n 1 \
        | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

api_key() {
    local k; k="$(ler_env EVOLUTION_API_KEY)"
    if [[ -z "$k" ]]; then
        echo "ERRO: EVOLUTION_API_KEY vazia no .env — sem ela a Evolution não sobe." >&2
        exit 1
    fi
    printf '%s' "$k"
}

instancia() {
    local i; i="$(ler_env WHATSAPP_INSTANCE)"
    printf '%s' "${i:-botofertas}"
}

# curl contra a Evolution SEM imprimir a chave em lugar nenhum: ela vai por
# stdin do curl (--config -), então não aparece na linha de comando, no
# histórico do shell nem em `ps`.
evo() {
    local metodo="$1" caminho="$2"; shift 2
    printf 'header = "apikey: %s"\n' "$(api_key)" \
        | curl -sS --config - -X "$metodo" "http://127.0.0.1:8080${caminho}" "$@"
}
