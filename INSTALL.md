# Instalação no Windows, pelo PowerShell

Do zero ao bot publicando, numa máquina limpa. Tudo por PowerShell — não há
passo em bash nem clique em janela de instalador além do Python.

## 1. Requisitos

| O quê | Versão | Obrigatório? |
|---|---|---|
| Windows | 10 ou 11 | sim |
| Python | 3.10 ou mais novo, com **Add python.exe to PATH** marcado | sim |
| Docker Desktop | qualquer | só para a Evolution API (ver passo 4) |

> O Windows já vem com um atalho chamado `python.exe` que **não é o Python**:
> ele abre a Microsoft Store. O `install.ps1` detecta isso e diz o que fazer —
> mas se quiser conferir antes, `python --version` tem que responder
> `Python 3.x`, não abrir a loja.

## 2. Instalar

```powershell
cd C:\bot_ofertas
.\install.ps1
```

Se o PowerShell recusar rodar o script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

O `install.ps1` faz, em 8 etapas:

1. confere o Python (versão mínima, e que não é o atalho da Store);
2. instala o `requirements.txt`;
3. instala o Chromium do Playwright (opcional — avisa e segue se falhar);
4. cria o `.env` a partir do `.env.example`, se ainda não existir;
5. cria a pasta `data\`;
6. sobe a Evolution API em Docker, se o Docker estiver rodando (opcional);
7. registra a tarefa `BotOfertas-AutoStart` (auto-start no login);
8. **verifica a instalação** com `python -m core.healthcheck`.

Etapa obrigatória que falha para o script na hora, dizendo o motivo. Etapa
opcional avisa e segue: o bot publica no Telegram sem Docker e sem Playwright.

Para não mexer no Docker: `.\install.ps1 -PularDocker`.

## 3. Preencher o `.env`

O `.env` nasce com os valores de **exemplo** — o bot não sobe assim. Preencha
no mínimo:

```ini
TOKEN_TELEGRAM=...      # o token do BotFather
CANAL_GERAL=-100...     # o ID do canal
```

Para publicar também no WhatsApp, `WHATSAPP_GROUP_ID` e `WHATSAPP_GROUP_NAME`.
Sem eles o Telegram publica normalmente e o WhatsApp fica parado — de
propósito.

Confira quando quiser, sem iniciar nada:

```powershell
python -m core.healthcheck
```

Ele lista item por item, marca `[FALHA]` no que a instalação ainda não
resolveu e sai com código 1 enquanto faltar algo obrigatório.

## 4. WhatsApp: dois caminhos

**No PC (padrão).** O envio usa o WhatsApp Desktop já instalado e logado.
Não precisa de Docker nem de Evolution API.

**Pela Evolution API (opcional).** Necessária onde não existe WhatsApp
Desktop. O `install.ps1` sobe o container e gera a `EVOLUTION_API_KEY` no
`.env`. Falta só ler o QR no celular, **uma vez**:

```powershell
python setup_whatsapp_api.py
```

Para subir o container à mão:

```powershell
docker compose --env-file .env -f docker\evolution.yml up -d
```

O `--env-file .env` não é enfeite: sem ele o `docker compose` procura o `.env`
dentro de `docker\` em vez da raiz do projeto, e recusa subir com
`required variable EVOLUTION_API_KEY is missing a value` mesmo com a chave
preenchida.

A porta 8080 fica só no loopback, de propósito: ela autentica com a mesma
chave que envia mensagem pelo número do dono. Para acessar de outra máquina,
túnel SSH.

## 5. Operação

```powershell
.\start.ps1      # inicia (sobe o processo pai startup.py)
.\status.ps1     # processos, /health, Evolution API, últimos erros
.\logs.ps1       # log ao vivo  (-Erros, -Subida, -Linhas N)
.\stop.ps1       # para
```

`start.ps1` confirma que o bot **continuou** de pé depois de subir: se o
`startup.py` morrer em seguida (o caso típico é `.env` incompleto), ele mostra
as últimas linhas do log e sai com erro, em vez de dizer "iniciado".

`stop.ps1` respeita uma rodada de publicação em andamento e recusa parar no
meio dela; para parar de qualquer jeito, `.\stop.ps1 -Forcar`. O container da
Evolution API continua no ar (é infraestrutura); para derrubá-lo também:
`docker compose --env-file .env -f docker\evolution.yml stop`.

## 6. Quando algo não sobe

| Sintoma | Causa provável |
|---|---|
| `python` abre a Microsoft Store | o `python.exe` do PATH é o atalho da Store; instale de python.org |
| `.\install.ps1` não roda | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `start.ps1` mostra log e sai com erro | `.env` incompleto — rode `python -m core.healthcheck` |
| `required variable EVOLUTION_API_KEY is missing` | faltou `--env-file .env` no `docker compose` |
| `status.ps1` diz `Healthcheck OFF` | o bot não está rodando; `.\start.ps1` |
| `.\logs.ps1` diz que o log não existe | o bot ainda não rodou nenhuma vez |

Diagnóstico completo para anexar num relato: `.\coletar_diagnostico.ps1`
(já sai com os segredos redigidos).
