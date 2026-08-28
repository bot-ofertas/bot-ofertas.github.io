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

## Ciclo diário: desliga e religa sozinho

```powershell
.\agendar_shutdown.ps1            # registra o ciclo
.\agendar_shutdown.ps1 -Status    # confere se está funcionando
.\agendar_shutdown.ps1 -Remover   # cancela
```

| Horário | O que acontece |
|---|---|
| 01:00 | Verificação diária — relatório de saúde no Telegram |
| 02:00 | Suspende o PC, **esperando até 35 min** se o bot estiver no meio de uma rodada (`core.database.execucao_em_andamento()`) |
| 02:00–08:45 | Quem publica é o GitHub Actions (`bot.yml`), 1x/hora |
| 08:45 | Wake timer acorda o PC, registra o despertar e sobe o `startup.py` |

**Suspensão (S3), não desligamento completo.** O Wake Timer do Windows não
acorda de um desligamento total (S5) sem "Wake on RTC" habilitado na BIOS —
acesso físico, fora do alcance de qualquer automação. Hibernação teria o
mesmo consumo do desligar, mas habilitá-la exige prompt de administrador que
esta automação não tem. Em S3 o consumo é de poucos watts durante a
madrugada, e o despertar é confiável.

Se quiser desligamento **completo**, habilite `Wake on RTC` / `RTC Alarm` na
BIOS e troque `SetSuspendState` por `shutdown /s` em `aguardar_e_desligar.ps1`.

O `-Status` mostra as três tarefas com `LastRunTime` e o código de resultado
traduzido, os wake timers ativos, o motivo do último despertar
(`powercfg -lastwake`) e as últimas linhas de `data/shutdown.log`. É o que
faltava quando o ciclo falhou em silêncio em 31/07/2026 — nem o desligamento
nem o despertar rodaram, e o único sintoma foi o PC ligado de manhã.

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
