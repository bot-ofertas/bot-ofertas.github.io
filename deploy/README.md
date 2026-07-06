# Deploy do Bot Ofertas na Nuvem (funciona com PC desligado)

## Por que precisa migrar?

O bot local depende de:
- PC ligado 24/7
- WhatsApp Desktop instalado no Windows
- Chrome/Playwright rodando

Para **funcionar com PC desligado**, tudo precisa rodar num servidor Linux com Docker.

## Opções de servidor

| Opção | Custo | Recomendado quando |
|-------|-------|--------------------|
| **Oracle Cloud Free Tier** | 🆓 Grátis para sempre | Começar (4 ARM cores + 24 GB RAM grátis) |
| **DigitalOcean Basic** | US$ 6/mês | Deploy simples com boa performance |
| **Hetzner CX11** | € 4,50/mês | Melhor custo-benefício na Europa |
| **AWS EC2 t2.micro** | 12 meses grátis | Free tier AWS |

## Passo a passo (VPS Ubuntu 22.04+)

### 1. Cria o servidor
- Escolha uma VPS acima
- Ubuntu 22.04 ou Debian 12
- Mínimo: 1 vCPU, 1 GB RAM, 10 GB disco

### 2. Conecta via SSH
```bash
ssh root@SEU_IP
```

### 3. Clona o projeto
```bash
apt update && apt install -y git
git clone https://github.com/ruan1989/bot_ofertas.git
cd bot_ofertas
```

### 4. Configura o .env
```bash
cp deploy/.env.example .env
nano .env
```

**Obrigatório preencher:**
- `TOKEN_TELEGRAM` — token do BotFather
- `CANAL_GERAL` — ID do canal Telegram
- `EVOLUTION_API_KEY` — string aleatória de 32+ caracteres
- `WHATSAPP_GROUP_ID` — só depois do passo 6

### 5. Sobe tudo (Docker + Evolution API + rastreador)
```bash
sudo bash deploy/deploy_vps.sh
```

O script:
- Instala Docker se não tiver
- Sobe container Evolution API na porta 8080
- Sobe container do rastreador
- Cria a instância `botofertas` no Evolution
- Mostra QR Code no terminal

### 6. Escaneia o QR
Opções para ver o QR:

**A) No navegador:**
```
http://SEU_IP:8080/manager
```

**B) Via curl (salva PNG):**
```bash
curl -H "apikey: SUA_EVOLUTION_API_KEY" \
     http://localhost:8080/instance/connect/botofertas \
     | jq -r '.base64' | base64 -d > qr.png
# Baixa o qr.png via SCP e escaneia no celular
```

### 7. Descobre o JID do grupo
```bash
curl -H "apikey: SUA_EVOLUTION_API_KEY" \
     "http://localhost:8080/group/fetchAllGroups/botofertas?getParticipants=false" \
     | jq -r '.[] | "\(.id)  \(.subject)"'
```

Copia o `120363XXXXXX@g.us` do grupo Bot-Ofertas.

### 8. Cola o JID no .env e reinicia
```bash
nano .env    # cola em WHATSAPP_GROUP_ID
docker compose -f deploy/docker-compose.vps.yml restart rastreador
```

## Comandos de manutenção

```bash
# Status
docker compose -f deploy/docker-compose.vps.yml ps

# Logs ao vivo
docker compose -f deploy/docker-compose.vps.yml logs -f rastreador
docker compose -f deploy/docker-compose.vps.yml logs -f evolution

# Reinicia após mudar .env
docker compose -f deploy/docker-compose.vps.yml restart

# Para tudo
docker compose -f deploy/docker-compose.vps.yml down

# Atualiza código do bot
git pull
docker compose -f deploy/docker-compose.vps.yml up -d --build
```

## Endpoints (para n8n)

- `http://SEU_IP:8724/health` — status dos componentes
- `http://SEU_IP:8724/errors?limit=50` — últimos erros JSON
- `http://SEU_IP:8724/stats` — estatísticas do banco

## Firewall

Abra as portas no security group / firewall:
- **8080** (Evolution API — precisa para escanear QR)
- **8724** (healthcheck para n8n — opcional, restringe por IP)

## Troubleshooting

**Evolution desconecta sozinho:**
Normal se o celular fica offline muito tempo. Escaneie o QR novamente:
```bash
curl -H "apikey: SUA_KEY" http://localhost:8080/instance/connect/botofertas
```

**Rastreador não posta:**
```bash
docker compose -f deploy/docker-compose.vps.yml logs --tail 50 rastreador
```

Ver últimos erros:
```bash
docker exec bot_rastreador cat /app/data/errors.jsonl | tail -20
```
