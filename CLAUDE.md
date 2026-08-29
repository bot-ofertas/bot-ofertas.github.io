# bot_ofertas — diretrizes operacionais

Bot de afiliados (Mercado Livre + Amazon Brasil) que raspa ofertas e publica em
Telegram e WhatsApp. Este arquivo é o guia de governança do projeto — precede
qualquer decisão de implementação nova.

## Regra 1 — Nunca regredir

Antes de criar ou alterar qualquer módulo: ler o código existente relacionado,
checar dependências, procurar duplicação. Nenhuma mudança pode quebrar uma
funcionalidade que já funciona. Preferir estender/corrigir a reescrever.

## Regra 2 — Autocorreção com evidência, não suposição

Ao detectar erro, exceção, gargalo ou comportamento inesperado: achar a causa
raiz com evidência real (log, query no banco, teste ao vivo isolado — não
suposição), corrigir, **validar** (`py_compile` + import real do módulo antes
de qualquer restart em produção — nunca só `ast.parse`), registrar a correção
e só então seguir. Ver histórico de commits para o padrão esperado de mensagem
(causa raiz → evidência → correção → verificação).

## Regra 3 — Mercado Livre

Todo link publicado deve carregar `matt_tool=47114387` como **parâmetro de
query real** — nunca confiar em checagem por substring (`"matt_tool=..." in
link`), sempre validar com `urllib.parse.urlsplit()` + `parse_qs()`. Cuidado
específico: URLs raspadas do carrossel/dinâmico do ML vêm com um `#fragment`
de tracking (`#polycard_client=...`) *antes* de qualquer query string —
qualquer limpeza de URL precisa fazer `split("?")[0].split("#")[0]`, nunca só
`split("?")[0]`, senão o parâmetro de afiliado fica preso dentro do fragmento
e nunca chega ao servidor do ML (bug real, corrigido em 2026-08-04, commit
`ed84736`). O mesmo cuidado vale para o ID estável de deduplicação de produto
— um fragmento não removido faz o mesmo produto virar um ID novo a cada
raspagem e ser republicado.

## Regra 4 — Amazon

Mesmo tratamento: toda URL publicada deve preservar `tag=silver1230c-20`
como parâmetro de query real, nunca substituído ou removido.

## Regra 5 — WhatsApp

O envio usa o **WhatsApp Desktop já instalado e logado do próprio Daniel**
neste PC (automação de UI da sessão autenticada dele, não acesso a conta de
terceiros nem bypass de login). Publica só no grupo configurado em
`WHATSAPP_GROUP_ID` (um grupo do próprio Daniel). Toda mensagem precisa sair
como uma unidade: foto + legenda (descrição + preço + desconto + link de
afiliado) juntos — nunca foto sozinha nem texto sozinho. Sem popup de
navegador quando o app desktop já está logado. Respeitar o intervalo
randômico de envio (30–45 min) para não parecer spam automatizado.

## Regra 6 — Telegram

Via Bot API oficial (`TOKEN_TELEGRAM`). Cada post: imagem + título + descrição
+ preço + desconto + link de afiliado + CTA. **Telegram nunca pode depender do
WhatsApp** — se o WhatsApp falhar, o Telegram publica normalmente.

## Regra 7 — Qualidade da oferta

Não publicar produto sem link de afiliado válido, sem imagem, ou com dados
incompletos (título/preço ausentes).

**Exceção decidida pelo Daniel em 2026-08-27 (foto indisponível):** quando o
CDN recusa o download do arquivo da foto, o post sai como texto com o
*preview nativo* do link — que carrega a imagem oficial do anúncio. O post
não fica sem imagem; o que se evita é perder a oferta inteira porque o
`mlstatic` devolveu 403. A tentativa de anexar a foto vem sempre primeiro
(alta resolução → variante 1x → download direto dos bytes), o fallback é o
último recurso, e cada ocorrência é registrada como
`telegram.publicado_sem_foto`. Para voltar ao comportamento estrito:
`PUBLICAR_SEM_FOTO=0` no `.env`.

## Regra 8 — Priorização

1. Corrigir erro relatado pelo Daniel (imediato, mesmo turno)
2. Concluir funcionalidade pendente já iniciada
3. Validar integrações (compile + import real, nunca só sintático)
4. Só depois: novos módulos/melhorias não solicitadas

## Regra 9 — Logs e diagnóstico

`data/bot.log` (log corrente ativo — checar este, não `data/startup.log` que
é legado de antes da migração para D:\), `data/errors.jsonl` (estruturado),
relatório legível em `C:\Users\Daniel\Desktop\problemas de execucao.txt`
(gerado por `gerar_relatorio_problemas.py`, atualizado toda madrugada por
`verificacao_diaria.py`). Healthcheck em `http://127.0.0.1:8724/health`.

## Regra 10 — Operacional (restart/deploy)

- Sempre checar `core.database.execucao_em_andamento()` antes de reiniciar ou
  desligar processos.
- `startup.py` é o processo **pai** que sobe os 3 rastreadores como filhos
  (`subprocess.Popen` simples, sem job object). Reiniciar só os filhos
  esgota o contador de tentativas (`falhas < 3`) do supervisor ao longo da
  sessão e pode derrubar o bot inteiro silenciosamente. **Sempre matar e
  relançar o processo pai** (`python -u startup.py`), nunca só os filhos.
- Nunca usar `-StartWhenAvailable` em nenhuma tarefa agendada de desligamento.
- Nunca digitar/inserir senha ou credencial em lugar nenhum, mesmo se
  fornecida explicitamente.
- Nunca alterar configuração de sistema/segurança do PC.

## Regra 11 — Limpeza de URL (afiliado + deduplicação)

- Nunca alterar o formato dos links de afiliado sem rodar `py_compile` +
  import real do módulo, e reconstruir um caso real do banco pra confirmar
  com `urllib.parse.parse_qs()` (não substring) que o parâmetro chegou como
  query de verdade.
- Toda limpeza de URL de produto usa `urllib.parse` ou, no mínimo,
  `.split("?")[0].split("#")[0]` — nunca só `split("?")`. Um `#fragment` de
  tracking do ML sobrevivendo quebra tanto o link de afiliado quanto o ID
  de deduplicação (bug real, 2026-08-04, commits `ed84736`/consequentes —
  chegou a causar ~26 reenvios duplicados de 2 produtos antes de corrigido).
- Toda mudança em lógica de deduplicação precisa de teste com URL contendo
  `?` e `#` simultaneamente, e idealmente usar o ID oficial do anúncio
  (ex.: `MLBU?-?\d+` do Mercado Livre, ASIN da Amazon) em vez de derivar da
  URL/slug quando esse ID estiver disponível.
- Toda integração externa (Mercado Livre, Amazon, Telegram, WhatsApp) deve
  reportar saúde própria em `/health` (ver `core/healthcheck.py`) e logar
  falhas via `core/error_logger.py` — nunca falhar silenciosamente.

## Regra 12 — Quarentena de publicação

Um produto que falha ao publicar **nunca** pode voltar indefinidamente à
rotação. Bug real (MLB68674214, 2026-08-25): 5 tentativas registradas entre
11:27 e 17:25, cada uma consumindo uma das 4 vagas de publicação da rodada,
porque `liberar_claim()` apagava a linha e o produto era raspado de novo na
rodada seguinte — sem contador, sem limite, sem fim.

- Toda falha de publicação chama `db.registrar_falha_publicacao()`.
- Ao atingir `MAX_TENTATIVAS_PUBLICACAO` (3), o produto entra em quarentena
  por `HORAS_QUARENTENA` (24) e os rastreadores o pulam via
  `db.em_quarentena()` **antes** de gastar qualquer chamada de rede.
- Publicação bem-sucedida chama `db.limpar_falha_publicacao()` — falha
  isolada de ontem não conta para o limite de hoje.
- A quarentena ativa aparece em `/health`, `/quarentena`, no `status.ps1` e
  no relatório de problemas da Área de Trabalho. Nada de produto sumindo em
  silêncio.

## Regra 13 — n8n

- A integração é **opcional e não-bloqueante**: sem `N8N_WEBHOOK_URL` tudo
  vira no-op e o bot roda exatamente como antes. Nenhuma publicação pode
  esperar, falhar ou atrasar por causa do n8n (mesma lógica da Regra 6).
- O bot **empurra** eventos (`integrations/n8n.py`); o n8n não consulta o
  bot. Desenho baseado em o n8n alcançar `127.0.0.1:8724` exige túnel/porta
  aberta e quebra na primeira troca de IP.
- Todo POST vai assinado (HMAC-SHA256 do corpo, `X-Bot-Assinatura`) e com
  `X-Bot-Token`. `POST /n8n/comando` altera estado (pausa, quarentena):
  sem `N8N_TOKEN` definido só aceita chamada de `127.0.0.1`.
- Evento que não sai vai para `data/n8n_spool.jsonl` (teto de 500) e é
  reenviado depois — queda de rede não pode perder o histórico.
- Segredo nenhum entra em `n8n/workflows/*.json`. Os tokens saem do `.env`
  local direto para o cofre de credenciais do n8n, via `n8n/setup_n8n.py`.
  Há teste automatizado que falha se um segredo aparecer nesses arquivos.

## Regra 14 — Rastreamento por canal

Cada canal publica o link com sua própria marcação de origem
(`matt_source` no ML, `ascsubtag` na Amazon, `utm_source` no resto) — é o
que responde "qual canal traz venda". A troca é sempre feita por
`core.tracking.marcar_origem()`, com `urllib.parse`, **nunca** por
`str.replace`: a versão anterior só funcionava quando o valor era
exatamente `bot_telegram` e virava no-op silencioso em qualquer outro caso.
`matt_tool`/`tag` são preservados sempre, e há teste que prova isso.

## Regra 15 — Ciclo diário do PC (liga 08:30 / desliga 02:00)

Decidido pelo Daniel em 2026-08-29. O PC opera das **08:30 às 02:00** e fica
desligado o resto. Os horários moram em **um lugar só**: `core/janela.py`
(lendo `HORA_LIGAR`/`HORA_DESLIGAR` do `.env`). Quem precisa deles pergunta —
`agendar_shutdown.ps1` via `python -m core.janela --agenda`, o `garantir_bot.py`
por import, os workflows do n8n via `setup_n8n.aplicar_janela()`. Nunca
reescrever um horário à mão num segundo arquivo: foi assim que o watchdog
passou a alertar "bot caiu" toda madrugada num desligamento planejado.

- **Desligar é suspender (S3), não `shutdown /s`.** Wake Timer não acorda de
  um S5 sem `Wake on RTC` na BIOS (confirmado em 2026-07-31: nem o
  desligamento nem o despertar rodaram, o PC só voltou no braço à noite).
  Trocar por desligamento completo quebra o religar automático — e um dia
  sem religar é um dia sem publicar nos grupos.
- **Nenhuma tarefa do ciclo usa `-StartWhenAvailable`**, exceto o supervisor.
  Nas outras, "recuperar" um gatilho perdido significa desligar o PC fora de
  hora (bug real de 2026-07-16). No supervisor é o oposto: recuperar é
  exatamente o trabalho dele, e ele não desliga nada.
- **O ciclo não pode depender de acertar um instante.** `BotOfertas-Supervisor`
  roda a cada 30 min e sobe o processo **pai** se o PC estiver ligado dentro
  da janela com o bot fora do ar — respeitando a pausa (`core.pausa`) e sem
  nunca subir um segundo bot (`startup.rastreador_em_execucao()`).
- **O watchdog do n8n cala a boca dentro da janela de silêncio** e, passados
  `MINUTOS_TOLERANCIA_RELIGAR` (45) do horário de religar sem heartbeat,
  manda o alerta que importa: *"o PC não religou"*. A marca
  `queda_planejada` só é consumida quando o bot volta ou quando o alerta
  sai — limpá-la antes faz o alerta genérico disparar contando as horas de
  sono planejado como se fossem queda.
- **Publicação diária tem piso na nuvem.** O workflow 02 publica às 09:00,
  12:00 e 20:00 BRT direto do n8n. Se o PC não voltar, os grupos ainda
  recebem oferta no mesmo dia.
