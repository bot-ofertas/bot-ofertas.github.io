# 🔥 Bot-Ofertas

Plataforma de curadoria de ofertas do **Mercado Livre** integrada com **Telegram** e **site**.
Coleta promoções, avalia qualidade, bloqueia ofertas suspeitas, gera link de afiliado oficial
e publica automaticamente — preservando a comissão em todas as URLs.

- **Canal Telegram:** publicação automática com selo de confiança e botões.
- **Site:** [bot-ofertas.github.io](https://bot-ofertas.github.io/) — vitrine com busca, filtros e 26 categorias.
- **Automação:** GitHub Actions roda a cada 45 min (07h–22h45 BRT).

---

## Como funciona (fluxo de uma oferta)

```
scraping → histórico de preço → deduplicação → validação anti-fraude
   → score (0-100) → classificação → link de afiliado → publicação (Telegram + site)
```

| Etapa | Módulo |
|-------|--------|
| Coleta de ofertas | `integrations/ml_browser.py` |
| Histórico de preço | `core/database.py` (`registrar_preco`, `historico_preco`) |
| Deduplicação | `core/database.py` (`link_ja_existe`) |
| Anti-fraude | `core/validador.py` |
| Score + classificação | `core/scorer.py` |
| Link de afiliado | `affiliates/mercadolivre.py` |
| Publicação Telegram | `integrations/telegram_bot.py` |
| Foto em alta resolução | `core/foto_url.py` |
| Token ML com renovação automática | `core/ml_token.py` |
| Origem por canal (matt_source) | `core/tracking.py` |
| Quarentena de publicação | `core/database.py` (`registrar_falha_publicacao`) |
| Eventos para o n8n | `integrations/n8n.py` |
| Anúncios de divulgação | `core/divulgacao.py` |
| Exportação para o site | `export_json.py` → `docs/data/offers.json` |

## Sinais de confiança (conversão)

- **Classificação** da oferta: 🔥 Imperdível / ✅ Boa / 👍 OK (a partir do score).
- **Selo "Oferta verificada"** — passou por validação anti-fraude + link de afiliado OK.
- **"Menor preço em 30 dias"** — quando o histórico de preço confirma.
- **Economia em R$** e **% de desconto** reais (sem desconto artificial).

---

## Rodar localmente

```bash
# 1. Dependências
pip install -r requirements.txt
python -m playwright install chromium

# 2. Credenciais — NUNCA versione o .env
cp .env.example .env
#   edite .env com o token do BotFather e o ID do canal

# 3. (uma única vez) login no portal de afiliados ML para gerar links meli.la
python -m affiliates.mercadolivre setup

# 4. Rodar o bot uma vez
python rastreador.py

#    ou em loop, a cada 60 min:
python rastreador.py --loop 60

# 5. Gerar o JSON do site a partir do banco
python export_json.py
```

### Chatbot do Telegram (modo polling, opcional)

```bash
python -c "from integrations.telegram_bot import criar_aplicacao; import os; \
criar_aplicacao(os.environ['TOKEN_TELEGRAM']).run_polling()"
```

Comandos: `/ofertas`, `/top`, `/celulares`, `/notebooks`, `/moda`, `/casa`, `/games`, …
Admin (restrito por `ADMIN_IDS`): `/status`, `/stats`.

---

## Grupos e divulgação

- **Telegram:** <https://t.me/ofertaseletronics>
- **WhatsApp:** <https://chat.whatsapp.com/JyJ9uLoZdE5LboH9GjAooC>
- **Página ponte:** <https://bot-ofertas.github.io/grupos/> — é para ela que
  os anúncios apontam. Ela repassa o `utm_source` da campanha para os links
  dos grupos, então dá para saber qual anúncio trouxe gente de verdade.

Todo post leva o CTA dos grupos, e cada canal publica o link com sua própria
marcação de origem (`matt_source=bot_telegram`, `bot_whatsapp`, `instagram`,
`meta_ads`…) — sempre preservando `matt_tool`/`tag`. Para gerar um anúncio
pronto:

```bash
python -m core.divulgacao instagram          # anúncio da rodada
python -m core.divulgacao facebook grupo     # divulgação pura do grupo
```

## WhatsApp: como ligar o envio para o grupo

O envio usa o **WhatsApp Desktop já logado nesta máquina** e publica só no
grupo configurado. Duas variáveis no `.env`, e só a primeira liga o envio:

| Variável | Para que serve |
|---|---|
| `WHATSAPP_GROUP_ID` | **Chave geral.** Vazio = a fila registra `wa_ativo=False` e não envia nada |
| `WHATSAPP_GROUP_NAME` | Nome do grupo **como aparece na lista de conversas** — é por ele que a automação acha a conversa (Ctrl+F), então precisa bater exatamente |

Para conferir se está de pé:

```powershell
.\status.ps1                             # visão geral
python diagnostico_whatsapp.py           # por que o grupo não está recebendo
python diagnostico_whatsapp.py --testar  # manda UMA mensagem agora, para conferir
```

O `diagnostico_whatsapp.py` percorre os oito elos entre a oferta e o grupo
(destino configurado → pausa → processo da fila → fila → método de envio →
dependências → `/health` → erros recentes), **para no primeiro que está
quebrado** e diz o que fazer. Existe porque cada elo registra num log
diferente e o sintoma é o mesmo em quase todos: nada acontece.

O `--testar` manda **uma** mensagem real ao grupo na hora, usando a oferta
mais recente do banco — sem ele, o único jeito de saber se a configuração
ficou certa é esperar o próximo envio da fila, 30 a 45 min depois de cada
reinício. Só age com a flag explícita, e recusa enviar se o diagnóstico
achou algum elo quebrado.

O `/health` pergunta primeiro se existe destino configurado e só depois se o
app está aberto. Antes ele olhava só o processo, então com o WhatsApp Desktop
rodando e nenhum grupo no `.env` mostrava **OK** enquanto o grupo não recebia
nada — verde na tela e zero postagem, sem nada explicando.

Cada mensagem sai como **uma unidade**: foto + legenda juntas. Sem foto, o
envio é abortado em vez de sair pela metade. O intervalo entre envios é
aleatório (30–45 min, em `whatsapp_queue_sender.py`): publicar no mesmo
instante do Telegram, sempre, é o padrão mais fácil de reconhecer como bot.

## Ciclo diário: desliga e religa sozinho

Um comando só — atualiza o código, registra as 4 tarefas e mostra o
resultado (ou duplo clique em `CICLO_DIARIO.bat`):

```powershell
.\configurar_ciclo.ps1            # traz o código, agenda e confere
```

Não depende do n8n: quem só quer a máquina ligando e desligando sozinha roda
este; `instalar_tudo.ps1` é para quem quer tudo junto. Por baixo:

```powershell
.\agendar_shutdown.ps1            # registra o ciclo
.\agendar_shutdown.ps1 -Status    # confere se está funcionando
.\agendar_shutdown.ps1 -Remover   # cancela
```

Os horários moram em `core/janela.py`, que lê `HORA_LIGAR`/`HORA_DESLIGAR`
do `.env`. Para mudar: edite o `.env` e rode `.\configurar_ciclo.ps1` de
novo — o agendamento, o supervisor e o watchdog do n8n leem todos daí, e é
por isso que não existe horário escrito à mão em segundo lugar.

| Horário | O que acontece |
|---|---|
| 01:00 | Verificação diária — relatório de saúde no Telegram |
| 02:00 | Suspende o PC, **esperando até 35 min** se o bot estiver no meio de uma rodada (`core.database.execucao_em_andamento()`) |
| 02:00–08:30 | PC dormindo. Publicam o GitHub Actions (`bot.yml`, 1x/hora) e o n8n na nuvem |
| 08:30 | Wake timer acorda o PC, registra o despertar e sobe o `startup.py` |
| 09:00 | Publicação de reforço pelo n8n — sai mesmo se o PC não tiver voltado |
| 09:15 | Se ainda não houve heartbeat, o watchdog avisa: **"o PC não religou"** |
| 09:30 | Relatório diário no Telegram |
| a cada 30 min | Supervisor: PC ligado, dentro da janela e sem bot rodando → sobe o bot |

Os horários vêm de `HORA_LIGAR`/`HORA_DESLIGAR` no `.env`, lidos por
`core/janela.py`. É a fonte única: as tarefas do Windows, o supervisor e a
janela de silêncio do watchdog no n8n saem todos daí. Mudou o horário? Rode
`.\agendar_shutdown.ps1` e `python n8n/setup_n8n.py --importar` e os três
acompanham.

**Suspensão (S3), não desligamento completo.** O Wake Timer do Windows não
acorda de um desligamento total (S5) sem "Wake on RTC" habilitado na BIOS —
acesso físico, fora do alcance de qualquer automação. Hibernação teria o
mesmo consumo do desligar, mas habilitá-la exige prompt de administrador que
esta automação não tem. Em S3 o consumo é de poucos watts durante a
madrugada, e o despertar é confiável — que é o que decide se os grupos
recebem oferta no dia seguinte.

Se quiser desligamento **completo**, habilite `Wake on RTC` / `RTC Alarm` na
BIOS e troque `SetSuspendState` por `shutdown /s` em `aguardar_e_desligar.ps1`.

### Três redes de segurança, porque o despertar pode falhar

Um ciclo que depende de uma tarefa acertar um instante falha inteiro quando
esse instante escapa — queda de energia, alguém desligando no botão, uma
atualização do Windows engolindo o gatilho. Foi o que aconteceu em
31/07/2026, e o único sintoma foi o PC ligado de manhã sem nada rodando.

1. **Supervisor a cada 30 min** (`garantir_bot.py`). Com o PC ligado dentro
   da janela e o bot fora do ar, ele sobe o processo **pai**. No pior caso o
   bot volta sozinho meia hora depois — sem ninguém por perto. Não age fora
   da janela, não passa por cima de uma pausa, não sobe um segundo bot.
2. **Alerta "o PC não religou"** no Telegram, 45 min após o horário de
   religar. É o aviso que chega enquanto ainda dá tempo de salvar o dia.
3. **Publicação na nuvem** (n8n, workflow 02) às 09:00, 12:00 e 20:00 BRT.
   Independe do PC: mesmo com a máquina fora do ar o dia todo, os grupos
   recebem oferta.

O `-Status` mostra as quatro tarefas com `LastRunTime` e o código de
resultado traduzido, os wake timers ativos, o motivo do último despertar
(`powercfg -lastwake`) e as últimas linhas de `data/shutdown.log`. É o que
faltava quando o ciclo falhou em silêncio em 31/07/2026.

## Automação em nuvem (n8n)

Cinco workflows prontos: ingestão de eventos + watchdog, publicação de
reforço, divulgação dos grupos, relatório diário e comandos remotos.

```bash
python n8n/setup_n8n.py --configurar  # monta o .env (gera o segredo, acha seu chat_id)
python n8n/setup_n8n.py --testar      # confere a conexão
python n8n/setup_n8n.py --importar    # cria credenciais, importa e ativa
```

O bot **empurra** os eventos para o n8n (não precisa abrir porta nem ter IP
fixo), e o watchdog no n8n avisa quando o heartbeat some — inclusive quando
o PC desliga. Detalhes, endpoints e solução de problemas: [`n8n/README.md`](n8n/README.md).

## Quando uma oferta falha ao publicar

Depois de 3 falhas seguidas, o produto entra em **quarentena** por 24h e sai
de rotação, em vez de voltar a cada rodada consumindo uma vaga de
publicação. Consulta e liberação:

```bash
curl http://127.0.0.1:8724/quarentena
curl -X POST http://127.0.0.1:8724/n8n/comando \
     -d '{"comando":"quarentena_liberar","dados":{"produto_id":"MLB123"}}'
```

A quarentena ativa aparece no `/health`, no `status.ps1` e no relatório de
problemas da Área de Trabalho.

## Testes

```bash
python tests/test_qualidade.py          # score, anti-fraude, afiliado
python tests/test_n8n_integracao.py     # n8n, quarentena, foto, tracking
python tests/test_sistema_completo.py   # bot + n8n montados (precisa do Node)
# ou, com pytest instalado:
python -m pytest tests/ -v
```

O último sobe o healthcheck e um "n8n" local que **executa os Code nodes
reais** dos workflows (com a static data persistida entre chamadas, como no
n8n de verdade) e percorre o caminho inteiro: evento assinado → n8n → alerta,
e comando do Telegram → n8n → API do bot. Tudo isso roda a cada pull request
pelo workflow `.github/workflows/testes.yml`.

Cobrem: cálculo de score, classificação, anti-fraude, **preservação do
parâmetro de afiliado** (`matt_tool`/`tag`) na troca de origem por canal,
normalização de foto para alta resolução, quarentena de publicação,
assinatura HMAC dos eventos e integridade dos workflows do n8n.

---

## Segurança

- Credenciais **somente** em `.env` (local) e **GitHub Secrets** (Actions) — nunca no código.
- `.env` está no `.gitignore` e não é versionado.
- Nenhuma oferta com desconto artificial é publicada (validador bloqueia descontos irreais).
- Todas as URLs preservam `matt_tool` (comissão de afiliado).

## Configuração do GitHub Actions

Defina os *Secrets* do repositório: `TOKEN_TELEGRAM`, `CANAL_GERAL`, `ML_AFFILIATE_TOOL_ID`.
O workflow (`.github/workflows/bot.yml`) roda o bot, atualiza `docs/data/offers.json` e faz commit automático.
