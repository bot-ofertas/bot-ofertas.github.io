#!/usr/bin/env bash
# deploy_vps.sh — Instala e sobe o bot num VPS Ubuntu/Debian
#
# Uso no VPS após clonar o repositório:
#   cd bot_ofertas/deploy
#   cp .env.example ../.env
#   nano ../.env       # preencha as credenciais
#   sudo bash deploy_vps.sh

set -euo pipefail

echo "============================================================"
echo "  BOT OFERTAS — DEPLOY VPS (Ubuntu/Debian)"
echo "============================================================"

# ─── 1. Instala Docker + Compose ─────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "[1/5] Instalando Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
fi

if ! docker compose version &>/dev/null; then
    echo "[1/5] Instalando docker compose..."
    apt-get update -y
    apt-get install -y docker-compose-plugin
fi
echo "[1/5] Docker OK: $(docker --version)"

# ─── 2. Valida .env ───────────────────────────────────────────────
if [[ ! -f ../.env ]]; then
    echo "[2/5] ERRO: arquivo .env não encontrado em $(dirname $(pwd))/.env"
    echo "        Rode: cp deploy/.env.example .env && nano .env"
    exit 1
fi
echo "[2/5] .env encontrado"

# ─── 3. Sobe os containers ────────────────────────────────────────
echo "[3/5] Subindo containers (evolution + rastreador)..."
cd "$(dirname "$0")"
docker compose -f docker-compose.vps.yml --env-file ../.env up -d --build

# ─── 4. Aguarda Evolution API ─────────────────────────────────────
echo "[4/5] Aguardando Evolution API..."
for i in {1..30}; do
    if curl -sf http://localhost:8080/ >/dev/null 2>&1; then
        echo "[4/5] Evolution API respondeu"
        break
    fi
    sleep 2
done

# ─── 5. Cria a instância do WhatsApp e mostra QR ─────────────────
API_KEY="$(grep '^EVOLUTION_API_KEY=' ../.env | cut -d= -f2)"
INSTANCE="$(grep '^WHATSAPP_INSTANCE=' ../.env | cut -d= -f2)"

echo "[5/5] Criando instância '$INSTANCE'..."
curl -sX POST "http://localhost:8080/instance/create" \
    -H "apikey: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"instanceName\":\"$INSTANCE\",\"integration\":\"WHATSAPP-BAILEYS\",\"qrcode\":true}" \
    | python3 -m json.tool 2>/dev/null || true

echo ""
echo "============================================================"
echo "  DEPLOY CONCLUÍDO"
echo "============================================================"
echo ""
echo "Próximos passos:"
echo "  1. Escaneie o QR Code:"
echo "     Acesse: http://SEU_IP:8080/manager"
echo "     Ou:     curl -H 'apikey: $API_KEY' http://localhost:8080/instance/connect/$INSTANCE"
echo ""
echo "  2. Depois de escanear, liste os grupos e copie o JID:"
echo "     curl -H 'apikey: $API_KEY' http://localhost:8080/group/fetchAllGroups/$INSTANCE?getParticipants=false"
echo ""
echo "  3. Cole o JID em WHATSAPP_GROUP_ID no .env e reinicie:"
echo "     docker compose -f deploy/docker-compose.vps.yml restart rastreador"
echo ""
echo "Comandos úteis:"
echo "  docker compose -f deploy/docker-compose.vps.yml ps       # status"
echo "  docker compose -f deploy/docker-compose.vps.yml logs -f  # logs ao vivo"
echo "  docker compose -f deploy/docker-compose.vps.yml restart  # reinicia"
echo "  docker compose -f deploy/docker-compose.vps.yml down     # para tudo"
echo ""
echo "Healthcheck para n8n:"
echo "  http://SEU_IP:8724/health"
echo "  http://SEU_IP:8724/errors"
echo "  http://SEU_IP:8724/stats"
