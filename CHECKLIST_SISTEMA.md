# Checklist do sistema — bot_ofertas

Atualizado em 2026-08-01. Referência rápida do que o sistema faz e do que é
"correto" para cada função — use para conferir rapidamente se algo saiu do
esperado.

## 1. Processos que devem estar sempre vivos

| Processo | Script | Intervalo | PID file |
|---|---|---|---|
| Rastreador ML | `rastreador.py --random` | 30–45 min | `data/rastreador.pid` |
| Rastreador Amazon | `rastreador_amazon.py --random` | 45–75 min | `data/rastreador_amazon.pid` |
| Campanha Ferramentas | `campanha_ferramentas.py --loop 15` | 15 min | `data/campanha_ferramentas.pid` |

Todos os 3 são subidos e monitorados por `startup.py` (`_Tracker` +
`monitorar()`): se um cair, reinicia sozinho com backoff exponencial
(30s → 60s → 120s → 300s, até 3 tentativas; contador zera após 2h estável).
Se os 3 desistirem ao mesmo tempo, o `startup.py` encerra e loga erro —
isso é o único cenário que exige intervenção manual.

**Correto:** os 3 aparecem "vivos" no relatório diário das 01:00
(`verificacao_diaria.py`). **Errado:** qualquer um marcado ❌ ali, ou
`erros_recentes` do `core.monitor.verificar_saude()` subindo sem parar.

## 2. Postagem — o que cada rastreador publica

- **ML (`rastreador.py`)**: diversidade por categoria (`MAX_POR_CATEGORIA=1`,
  até `MAX_POR_EXECUCAO=4`/rodada), 26 categorias em `integrations/ml_browser.py`.
  Usa foto real do produto sempre.
- **Amazon (`rastreador_amazon.py`)**: até 3/rodada, só cupons com desconto
  calculável passam por padrão (score mínimo 40, +15 se tiver cupom real).
  Desde a correção de 2026-08-01: só usa o banner genérico "ALERTA CUPOM"
  quando `item.get("cupom")` é verdadeiro — senão publica com a foto real do
  produto, igual ao ML.
- **Campanha Ferramentas (`campanha_ferramentas.py`)**: mínimo 6/rodada,
  categorias `/c/ferramentas` e `/c/construcao`, filtradas por palavra-chave
  (`_PALAVRAS_FERRAMENTA`) pra excluir itens fora do tema (fechadura, cuba
  etc). Desconto mínimo 10%, score mínimo 50.

**Correto:** `affiliate_taxa` em 100% (ou muito perto) no relatório diário.
**Errado:** `affiliate_taxa` < 50% (dispara alerta automático via
`verificar_e_alertar()`).

## 3. Links de afiliado — regra inegociável

- **Mercado Livre:** todo link deve conter `matt_tool=47114387`.
- **Amazon:** todo link deve conter `tag=silver1230c-20`.

Isso é validado e reforçado no pipeline de geração de link
(`core/*` + `integrations/amazon_paapi.py` para enriquecimento opcional via
PA-API, quando configurada). Taxa de sucesso monitorada em
`affiliate_status` na tabela `produtos`.

## 4. Canais de publicação

| Canal | Depende de | Observação |
|---|---|---|
| Telegram | `TOKEN_TELEGRAM` + `CANAL_GERAL` | **Nunca** depende de WhatsApp — sempre publica mesmo se o WhatsApp cair. |
| WhatsApp | WhatsApp Desktop nativo aberto | Best-effort; watchdog reabre o app se cair (`core/wa_desktop_watchdog.py`, checa a cada 60s). Fallback via Chrome dedicado só se `WHATSAPP_CHROME_FALLBACK=1` (desativado por padrão). |
| Instagram | `INSTAGRAM_USERNAME`/senha no `.env` | Ativo. Logo "Ofertas de Tudo" ainda **não aplicada** como foto de perfil (task #9, aguardando arquivo da logo do Daniel). |
| Twitter/X | `TWITTER_API_KEY` etc. | **Não configurado** (task #12). |
| Facebook | `FACEBOOK_PAGE_ID` etc. | **Não configurado** (task #12). |
| Site (GitHub Pages) | `web/` + `core/blog_generator.py` | Gera landing page por produto publicado, best-effort — nunca derruba a publicação principal se falhar. |

## 5. Ciclo diário de energia (agendar_shutdown.ps1)

| Horário | Tarefa | O que faz |
|---|---|---|
| 01:00 | `BotOfertas-VerificacaoDiaria` | Roda `verificacao_diaria.py` — relatório de saúde por Telegram + alertas reativos. |
| 02:00 | `BotOfertas-Shutdown` | Roda `aguardar_e_desligar.ps1` — espera até 35min se o bot estiver ocupado (`verificar_ocioso.py`), depois **suspende** (S3, não desliga 100%). |
| 08:30 | `BotOfertas-WakeUp` | Acorda o PC via Wake Timer e roda `acordar_e_iniciar.ps1`, que registra o despertar e sobe o bot pelo `garantir_bot.py`. |
| 30/30min | `BotOfertas-Supervisor` | Se o PC está ligado dentro da janela e o bot não está rodando, sobe o processo pai. É a rede de segurança do ciclo. |

Os horários saem de `core/janela.py` (`HORA_LIGAR`/`HORA_DESLIGAR` do `.env`) —
mudar no `.env` e rodar `.\configurar_ciclo.ps1` de novo reagenda tudo.

**Por que suspensão e não desligamento completo:** confirmado em
2026-07-31 que shutdown completo (S5) não acorda via Wake Timer do Windows
sem "Wake on RTC" habilitado na BIOS (acesso físico, fora do alcance
remoto). Suspensão (S3) acorda de forma confiável via software, mesmo sem
admin nem acesso à BIOS — troca é consumo baixíssimo (poucos watts) durante
a madrugada em vez de zero.

**Regra permanente:** nenhuma tarefa de shutdown (diária ou única) pode usar
`-StartWhenAvailable` — causou desligamento fora de hora uma vez (bug de
2026-07-16: o Windows "recuperava" o gatilho perdido assim que o PC ligava
de novo).

**Correto:** `NextRunTime` das 3 tarefas sempre no futuro próximo (checar com
`Get-ScheduledTask 'BotOfertas-*' | Get-ScheduledTaskInfo`). **Errado:**
`LastRunTime` congelado sem bater com o horário configurado — sinal de que a
tarefa não disparou de verdade.

## 6. Proteção contra desligar/dormir no meio de uma operação

Todo processo que publica (ML, Amazon, Ferramentas) chama
`db.iniciar_execucao()` / `db.finalizar_execucao()` ao redor de cada rodada.
`verificar_ocioso.py` / `aguardar_e_desligar.ps1` consultam
`execucao_em_andamento(minutos_max=20)` antes de suspender — se alguma
execução está em andamento há menos de 20 min, adia por até 35 min no total.

## 7. O que NUNCA fazer automaticamente (regras de segurança permanentes)

- Nunca digitar/inserir senha ou credencial em nenhum sistema, campo ou
  prompt — mesmo se o usuário fornecer a senha e autorizar explicitamente.
- Nunca alterar configurações de segurança do sistema (bloqueio de tela,
  hibernação, wake-arming de dispositivo, senha da conta) diretamente — só
  fornecer os comandos exatos para o próprio usuário rodar.
- Nunca usar `-StartWhenAvailable` em tarefa de shutdown.
- Telegram/WhatsApp nunca dependem um do outro para publicar.

## 8. Pendências conhecidas (não são falhas de código)

- Amazon publica bem menos que o ML porque o catálogo de cupons/ofertas da
  Amazon Brasil é mais estreito — não é processo travado (rodadas completam
  no horário certo), é saturação de duplicatas. Ver task #15 para proposta
  de ampliar o escopo de busca.
- DigitalOcean, Twitter/X, Facebook e a logo do Instagram seguem bloqueados
  em ações que só o Daniel pode fazer (pagamento, credenciais, arquivo da
  logo).
