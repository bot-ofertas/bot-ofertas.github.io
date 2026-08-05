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
