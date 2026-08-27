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

## Automação em nuvem (n8n)

Cinco workflows prontos: ingestão de eventos + watchdog, publicação de
reforço, divulgação dos grupos, relatório diário e comandos remotos.

```bash
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
python tests/test_qualidade.py         # sem dependências extras
python tests/test_n8n_integracao.py    # idem
# ou, com pytest instalado:
python -m pytest tests/ -v
```

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
