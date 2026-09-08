#!/usr/bin/env bash
# INSTALADOR — sobe o bot num servidor Ubuntu/Debian (DigitalOcean e afins).
#
# Rode da raiz do repositório, no servidor:
#
#     cat .env.example deploy/.env.example > .env
#     nano .env                      # preencha os valores reais
#     sudo bash deploy/deploy_vps.sh
#
# É seguro rodar de novo: instala o que falta, reconstrói as imagens e sobe
# o que estiver parado, sem apagar volume (o banco de deduplicação e a sessão
# do WhatsApp sobrevivem).

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_comum.sh"

echo "════════════════════════════════════════════════════════════"
echo "  BOT OFERTAS — instalação no servidor"
echo "════════════════════════════════════════════════════════════"

# ─── 1. Docker ────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "[1/5] Instalando Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
fi
if ! docker compose version &>/dev/null; then
    echo "[1/5] Instalando o plugin docker compose..."
    apt-get update -y
    apt-get install -y docker-compose-plugin
fi
echo "[1/5] Docker OK — $(docker --version)"

# ─── 2. .env ──────────────────────────────────────────────────────────
exigir_env
api_key >/dev/null      # aborta cedo, com mensagem, se a chave estiver vazia
echo "[2/5] .env OK — $ENV_FILE"

PAPEL_ATUAL="$(ler_env PAPEL)"; PAPEL_ATUAL="${PAPEL_ATUAL:-nuvem}"
echo "       papel deste servidor: $PAPEL_ATUAL"
if [[ "$PAPEL_ATUAL" == "nuvem" ]]; then
    echo "       (publica só enquanto o PC local está desligado — troque para"
    echo "        nuvem-exclusiva depois de calar o PC e o GitHub Actions)"
fi

# ─── 3. Containers ────────────────────────────────────────────────────
echo "[3/5] Construindo e subindo os containers..."
dc up -d --build

# ─── 4. Espera a Evolution responder ──────────────────────────────────
echo "[4/5] Aguardando a Evolution API..."
pronta=0
for _ in $(seq 1 45); do
    if curl -sf -o /dev/null http://127.0.0.1:8080/ ; then pronta=1; break; fi
    sleep 2
done
if [[ "$pronta" != "1" ]]; then
    echo "[4/5] A Evolution não respondeu em 90s. O Telegram já está publicando"
    echo "      (ele nunca depende do WhatsApp). Veja o que houve com:"
    echo "         bash deploy/botctl.sh logs evolution"
else
    echo "[4/5] Evolution respondeu"
fi

# ─── 5. Instância do WhatsApp ─────────────────────────────────────────
INSTANCIA="$(instancia)"
if [[ "$pronta" == "1" ]]; then
    echo "[5/5] Garantindo a instância '$INSTANCIA'..."
    # Já existir não é erro: o script é feito para rodar de novo.
    evo POST /instance/create \
        -H "Content-Type: application/json" \
        -d "{\"instanceName\":\"$INSTANCIA\",\"integration\":\"WHATSAPP-BAILEYS\",\"qrcode\":true}" \
        >/dev/null 2>&1 || true
    echo "[5/5] Instância pronta"
fi

cat <<MSG

════════════════════════════════════════════════════════════
  SUBIU
════════════════════════════════════════════════════════════

O Telegram já está publicando. Faltam duas coisas que só você pode fazer,
porque envolvem o seu celular e a sua conta:

  1) Conectar o WhatsApp (uma vez só — a sessão fica no volume Docker):

       bash deploy/botctl.sh qr

     Isso salva um PNG e mostra o QR no terminal. Leia com
     WhatsApp → Aparelhos conectados → Conectar aparelho.

  2) Descobrir o ID do grupo e colar no .env:

       bash deploy/botctl.sh grupos
       nano .env                     # WHATSAPP_GROUP_ID=120363...@g.us
       bash deploy/botctl.sh reiniciar

Enquanto WHATSAPP_GROUP_ID estiver vazio, o WhatsApp fica desligado e o
Telegram publica normalmente (Regra 6).

Dia a dia:
  bash deploy/botctl.sh status      # o que está de pé, papel, saúde
  bash deploy/botctl.sh logs        # logs ao vivo
  bash deploy/botctl.sh atualizar   # puxa do GitHub e reconstrói
MSG
