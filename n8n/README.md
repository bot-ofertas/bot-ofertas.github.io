# n8n — automação em nuvem do Bot Ofertas

Cinco workflows prontos, um instalador que os sobe pela API e a ponte de
eventos no lado do bot. Tudo importável e ativável com **um comando**.

---

## Como as duas metades conversam

```
   PC do Daniel (ou VPS)                          n8n (nuvem ou self-hosted)
┌───────────────────────────┐   POST assinado  ┌──────────────────────────────┐
│ rastreador / campanha /   │ ───────────────► │ 01  Ingestão + watchdog      │
│ Amazon / fila WhatsApp    │  oferta, erro,   │ 02  Publicação de reforço    │
│                           │  heartbeat…      │ 03  Divulgação dos grupos    │
│ healthcheck :8724         │ ◄─────────────── │ 04  Relatório diário         │
│  /n8n/comando (opcional)  │  comandos        │ 05  Comandos remotos         │
└───────────────────────────┘                  └──────────────────────────────┘
```

**O bot fala primeiro.** O healthcheck vive em `127.0.0.1:8724`, atrás do
roteador, com IP dinâmico — nenhum n8n na nuvem alcançaria isso. Então cada
evento do bot vira um `POST` para um webhook do n8n. Não é preciso abrir
porta, contratar IP fixo nem manter túnel: funciona com o n8n.cloud como
está.

O caminho de volta (workflow 05) é **opcional** e só funciona se o
healthcheck estiver acessível pelo n8n — self-hosted na mesma máquina
(`host.docker.internal`), VPS, ou túnel. Sem isso, os outros quatro
workflows continuam funcionando inteiros.

---

## Instalação em 5 passos

### 1. Tenha um n8n

**Nuvem:** crie a conta em [n8n.cloud](https://n8n.io/cloud/) — nada a
instalar.

**Self-hosted (mesma máquina):**

```bash
cp n8n/.env.n8n.example n8n/.env.n8n     # preencha senha e chave de cifra
docker compose -f n8n/docker-compose.n8n.yml --env-file n8n/.env.n8n up -d
```
Abra <http://localhost:5678> e crie a conta de administrador.

> Se a conta de administrador ficou pela metade numa tentativa anterior:
> abra `http://localhost:5678/setup` de novo e conclua. Se o formulário não
> aparecer, a conta já existe — use `http://localhost:5678/signin`.

### 2. Gere a API key do n8n

No n8n: **Settings → n8n API → Create an API key**. Copie a chave.

### 3. Preencha o `.env` do bot

Deixe o próprio instalador montar o que der:

```bash
python n8n/setup_n8n.py --configurar
```

Ele **gera** o `N8N_TOKEN` (32 bytes aleatórios), **descobre** o seu
`ADMIN_CHAT_ID` perguntando ao Telegram quem já falou com o bot, e escreve
tudo no `.env` sem apagar seus comentários nem o que já estava preenchido.
Sobra um único campo manual — a API key, que só existe depois de você criá-la
na interface.

Se preferir à mão:

```ini
N8N_API_URL=http://localhost:5678          # ou https://SEU-ESPACO.app.n8n.cloud
N8N_API_KEY=cole-a-chave-aqui
N8N_TOKEN=um-segredo-longo-que-voce-inventa  # autentica os webhooks
ADMIN_CHAT_ID=123456789                    # seu chat pessoal, recebe alertas
```

Para descobrir seu `ADMIN_CHAT_ID`: mande `/start` para o seu bot e abra
`https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` — o número em
`message.chat.id` é ele.

### 4. Rode o instalador

```bash
python n8n/setup_n8n.py --testar      # confere conexão e configuração
python n8n/setup_n8n.py --importar    # cria as credenciais, importa e ativa
```

**Não achou a tela da API key?** Ela muda de lugar entre versões do n8n, e
foi onde uma instalação real travou. Existe um caminho que não precisa de
chave nenhuma:

```bash
python n8n/setup_n8n.py --preparar
```

Isso grava em `n8n/prontos/` os mesmos JSON que o `--importar` enviaria —
com `admin_chat_id`, canal e a janela de operação já preenchidos — sem
tocar na rede. Daí:

```bash
n8n import:workflow --separate --input=n8n/prontos   # a CLI do próprio n8n
```

ou, pela interface, **Workflows → ⋯ → Import from File**, um por vez. Nos
dois casos sobram duas coisas para fazer à mão, que só a API faz sozinha:
criar as credenciais (**Bot Ofertas — Telegram** e **Bot Ofertas — Token do
Webhook**, Header Auth com header `X-Bot-Token`) e ativar cada workflow.

### Atalho: um comando para tudo

```powershell
.\instalar_tudo.ps1        # ou duplo-clique em INSTALAR_TUDO.bat
```

Atualiza o código, preenche o `.env`, instala os workflows (pela API se
houver chave, por arquivo se não houver), registra o ciclo diário e
confere o bot. Cada etapa é independente: uma que falhe não impede as
seguintes, e o resumo do fim lista o que ficou pendente.

O instalador:

- cria no cofre do n8n as credenciais **Bot Ofertas — Telegram**
  (`TOKEN_TELEGRAM`) e **Bot Ofertas — Token do Webhook** (`N8N_TOKEN`) —
  os segredos saem do seu `.env` direto para o n8n, nunca passam por
  arquivo versionado;
- preenche `admin_chat_id`, `canal` e `api_bot` dentro dos workflows;
- cria ou atualiza cada workflow **pelo nome** (rodar de novo não duplica);
- ativa os workflows e imprime a URL do webhook.

### 5. Ligue o bot no n8n

Cole no `.env` a URL que o instalador imprimiu e reinicie pelo processo
**pai** (Regra 10 do `CLAUDE.md` — reiniciar só os filhos esgota o contador
do supervisor):

```ini
N8N_WEBHOOK_URL=https://SEU-N8N/webhook/bot-ofertas
N8N_ATIVO=1
```

```bash
python -u startup.py
python -m integrations.n8n      # envia um evento de teste e mostra o resultado
```

Confira em `http://127.0.0.1:8724/health` → bloco `n8n`.

---

## Os workflows

| # | Nome | Dispara com | O que faz |
|---|------|-------------|-----------|
| 01 | Ingestão de eventos e watchdog | webhook + a cada 15 min | Recebe todo evento do bot, acumula estatísticas, alerta no Telegram em quarentena/rodada falhada/erro. O ramo agendado avisa quando o **heartbeat some** — é assim que você fica sabendo que o PC caiu. |
| 02 | Publicação de reforço | 09h, 12h e 20h BRT | Lê `offers.json` do site e publica no canal um resumo "TOP 3 de agora" com CTA do grupo. Roda com o PC desligado — é o piso que garante postagem diária mesmo se a máquina não religar. |
| 03 | Divulgação dos grupos | 10h, 16h e 19h BRT | Monta o anúncio de divulgação (rodando entre Instagram, TikTok e Facebook) e entrega pronto no seu chat, com a foto sugerida. |
| 04 | Relatório diário e saúde | 09h30 BRT | Junta o estado do site com as últimas execuções do GitHub Actions e manda o resumo — problemas na primeira linha. Roda uma hora depois do religar, para retratar o PC já de pé. |
| 05 | Comandos remotos | mensagem no Telegram | `/status`, `/pausar`, `/retomar`, `/quarentena`, `/liberar <id>`, `/erros`, `/divulgar`, `/ping`. Exige `BOT_API_URL` acessível. |

### Por que o watchdog fica no n8n e não no bot

Um alerta gerado pelo próprio bot nunca chega justamente no caso que
importa: o bot morto, o PC desligado, a internet caída. O heartbeat inverte
isso — o n8n espera o sinal e reclama da **ausência** dele. O alerta só sai
na transição (uma vez), não a cada 15 minutos, e a volta também é avisada.

### A janela de silêncio (02:00 → 08:30)

O PC desliga às 02:00 e volta às 08:30. Nessas 6h30 a ausência de heartbeat
é o comportamento contratado, não uma falha — sem tratar isso, o watchdog
mandaria um par "🔴 caiu / 🟢 voltou" **todo dia**, e em uma semana o alerta
já teria virado ruído que ninguém abre. Dentro da janela ele fica calado e
marca a queda como planejada.

O valor está no que vem depois: passados 45 min do horário de religar sem
nenhum sinal, sai o alerta **"o PC não religou"** — o único momento em que
a diferença entre desligado e quebrado importa de verdade, porque é o dia
inteiro de publicação que está em jogo.

Os horários não são digitados nos JSON: `n8n/setup_n8n.py` sincroniza
`silencio_de`/`silencio_ate` a partir de `core/janela.py` (que lê
`HORA_LIGAR`/`HORA_DESLIGAR` do `.env`) a cada `--importar`. Os arquivos
versionados já vêm com os valores certos preenchidos, então importar à mão
pela interface também produz um watchdog correto.

---

## Eventos que o bot envia

| Evento | Quando | Campos principais |
|--------|--------|-------------------|
| `heartbeat` | a cada 5 min | `fila_whatsapp`, `erros_10min`, `quarentena`, `spool` |
| `oferta_publicada` | publicou no Telegram | `produto_id`, `titulo`, `preco`, `desconto_pct`, `link`, `foto`, `fonte` |
| `produto_quarentena` | 3ª falha seguida | `produto_id`, `tentativas`, `quarentena_ate`, `mensagem` |
| `rodada_concluida` | fim de cada rodada | `publicados`, `duplicatas`, `erros`, `links_falharam`, `duracao_s` |
| `rodada_falhou` | exceção na rodada | `erro`, `fonte` |
| `rodada_pulada` | sem DNS | `motivo` |
| `bot_reiniciado` | supervisor subiu o bot | `origem`, `motivo`, `dentro_da_janela` |
| `erro` | erro registrado | `operacao`, `mensagem`, `arquivo`, `linha` (1 por operação a cada 5 min) |

Todo POST leva `X-Bot-Assinatura: sha256=<HMAC do corpo>` e
`X-Bot-Token: <N8N_TOKEN>`. O nó Webhook autentica pelo header antes de
executar qualquer nó; a assinatura HMAC fica disponível para quem quiser
conferir também o corpo.

**Nada se perde em queda de rede.** Evento que não sai vai para
`data/n8n_spool.jsonl` e é reenviado no próximo envio bem-sucedido (teto de
500 eventos, os mais antigos são descartados).

---

## Endpoints do bot que o n8n consome

| Método e rota | Serve para |
|---|---|
| `GET /health` | saúde de cada componente (inclui `n8n`, `pausa`, `quarentena`) |
| `GET /stats` | números do banco |
| `GET /errors?limit=50` | últimos erros em JSON |
| `GET /quarentena` | produtos fora de rotação |
| `GET /divulgacao?rede=instagram&tipo=auto` | texto do anúncio pronto |
| `GET /metrics` | formato Prometheus |
| `POST /oferta` | publicar uma oferta avulsa |
| `POST /alerta` | registrar um alerta vindo do n8n |
| `POST /n8n/comando` | executar comando (autenticado) |

`POST /n8n/comando` aceita `X-Bot-Token` **ou** `X-Bot-Assinatura`. Sem
`N8N_TOKEN` definido, só aceita chamada de `127.0.0.1` — um endpoint que
pausa a operação não pode ficar aberto na rede local sem segredo.

Comandos: `status`, `quarentena_listar`, `quarentena_liberar`, `pausar`,
`retomar`, `divulgacao`, `erros`, `flush_spool`, `ping`.

```bash
curl -X POST http://127.0.0.1:8724/n8n/comando \
     -H "X-Bot-Token: $N8N_TOKEN" \
     -d '{"comando":"status"}'
```

### Expor o healthcheck para um n8n externo

Só se você quiser o workflow 05. Duas opções:

- **n8n self-hosted na mesma máquina:** use
  `BOT_API_URL=http://host.docker.internal:8724` e
  `HEALTHCHECK_BIND=0.0.0.0` no `.env` do bot.
- **n8n na nuvem:** um túnel (`cloudflared tunnel --url
  http://127.0.0.1:8724`) e `BOT_API_URL` apontando para a URL do túnel.

Em qualquer um dos dois, **defina `N8N_TOKEN` antes**: mudar o bind sem
segredo deixa `/n8n/comando` aberto.

---

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `401` no `setup_n8n.py` | `N8N_API_KEY` ausente ou revogada |
| "não consegui falar com o n8n" | URL errada, ou o contêiner não subiu |
| Workflow importa mas não ativa | falta credencial no nó de trigger — a mensagem do n8n diz qual |
| Webhook responde `403` | `N8N_TOKEN` do `.env` diferente do valor da credencial de header |
| Alertas não chegam | `ADMIN_CHAT_ID` vazio, ou você nunca mandou `/start` para o bot |
| `spool_pendente` crescendo no `/health` | o n8n está fora do ar ou a URL do webhook mudou |

Para desligar a integração inteira sem mexer em código: `N8N_ATIVO=0` no
`.env`. O bot volta a rodar exatamente como antes.
