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

## Testes

```bash
python tests/test_qualidade.py        # sem dependências extras
# ou, com pytest instalado:
python -m pytest tests/ -v
```

Cobrem: cálculo de score, classificação, anti-fraude e **preservação do parâmetro de afiliado** (`matt_tool`).

---

## Segurança

- Credenciais **somente** em `.env` (local) e **GitHub Secrets** (Actions) — nunca no código.
- `.env` está no `.gitignore` e não é versionado.
- Nenhuma oferta com desconto artificial é publicada (validador bloqueia descontos irreais).
- Todas as URLs preservam `matt_tool` (comissão de afiliado).

## Configuração do GitHub Actions

Defina os *Secrets* do repositório: `TOKEN_TELEGRAM`, `CANAL_GERAL`, `ML_AFFILIATE_TOOL_ID`.
O workflow (`.github/workflows/bot.yml`) roda o bot, atualiza `docs/data/offers.json` e faz commit automático.
