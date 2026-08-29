# -*- coding: utf-8 -*-
"""
SETUP N8N — instala e ativa os workflows do Bot Ofertas numa instância n8n.

Faz pela API pública do n8n exatamente o que você faria clicando no
navegador, e de forma repetível:

  1. cria (uma vez) as credenciais que os workflows usam:
       • "Bot Ofertas — Telegram"          (token vindo do .env: TOKEN_TELEGRAM)
       • "Bot Ofertas — Token do Webhook"  (segredo do .env: N8N_TOKEN)
  2. preenche os campos de CONFIG dentro dos nós Code (chat do admin, canal,
     URL da API do bot) com os valores do .env;
  3. cria ou ATUALIZA cada workflow de n8n/workflows/*.json pelo nome;
  4. ativa os workflows;
  5. imprime a URL do webhook para colar em N8N_WEBHOOK_URL.

Nenhum segredo é escrito nos arquivos do repositório: os tokens saem do seu
.env local direto para o cofre de credenciais do n8n. Os ids das credenciais
criadas ficam em n8n/.n8n_state.json (ignorado pelo git) para que rodar de
novo não duplique nada.

Uso:
    python n8n/setup_n8n.py --configurar  # monta o .env: gera o segredo do
                                          # webhook e DESCOBRE seu chat_id
    python n8n/setup_n8n.py --testar      # só confere conexão e configuração
    python n8n/setup_n8n.py --importar    # cria/atualiza e ativa tudo
    python n8n/setup_n8n.py --importar --sem-ativar
    python n8n/setup_n8n.py --listar      # o que já existe na instância

Variáveis usadas (.env na raiz do projeto):
    N8N_API_URL=http://localhost:5678     # endereço do seu n8n
    N8N_API_KEY=...                       # Settings → n8n API → Create API key
    N8N_TOKEN=...                         # segredo do webhook (você inventa)
    TOKEN_TELEGRAM=...                    # já usado pelo bot
    CANAL_GERAL=@ofertaseletronics        # canal onde as ofertas são postadas
    ADMIN_CHAT_ID=...                     # seu chat pessoal (recebe alertas)
    BOT_API_URL=                          # opcional: healthcheck acessível de fora
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib import error, request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# O instalador é chamado como `python n8n/setup_n8n.py`, então quem entra no
# sys.path é a pasta n8n/, não a raiz — sem esta linha, `from core import
# janela` (usado para sincronizar a janela de operação nos workflows) falha.
sys.path.insert(0, BASE)
DIR_WORKFLOWS = os.path.join(BASE, "n8n", "workflows")
ESTADO_PATH = os.path.join(BASE, "n8n", ".n8n_state.json")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
except ImportError:  # o script funciona sem python-dotenv, só com env vars
    pass


# ── Cliente HTTP mínimo da API do n8n ────────────────────────────────────────

class N8nErro(RuntimeError):
    pass


def _api_url() -> str:
    return (os.getenv("N8N_API_URL") or "http://localhost:5678").strip().rstrip("/")


def _api_key() -> str:
    return (os.getenv("N8N_API_KEY") or "").strip()


def _chamar(metodo: str, caminho: str, corpo: dict | None = None) -> dict:
    """Requisição à API do n8n. Usa urllib para não depender de `requests`
    (este script costuma rodar num Python enxuto, fora do venv do bot)."""
    url = f"{_api_url()}/api/v1{caminho}"
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8") if corpo is not None else None
    req = request.Request(url, data=dados, method=metodo)
    req.add_header("X-N8N-API-KEY", _api_key())
    req.add_header("Accept", "application/json")
    if dados:
        req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=30) as r:
            texto = r.read().decode("utf-8")
            return json.loads(texto) if texto.strip() else {}
    except error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:400]
        if e.code == 401:
            raise N8nErro(
                "401 — API key inválida ou ausente. Gere uma em "
                "Settings → n8n API e coloque em N8N_API_KEY no .env."
            ) from e
        raise N8nErro(f"{metodo} {caminho} → HTTP {e.code}: {detalhe}") from e
    except error.URLError as e:
        raise N8nErro(
            f"Não consegui falar com o n8n em {_api_url()} ({e.reason}). "
            "O n8n está rodando? A URL está certa?"
        ) from e


# ── Estado local (ids de credencial já criados) ──────────────────────────────

def _ler_estado() -> dict:
    try:
        with open(ESTADO_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _gravar_estado(estado: dict) -> None:
    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


# ── Credenciais ──────────────────────────────────────────────────────────────

CRED_TELEGRAM = "Bot Ofertas — Telegram"
CRED_HEADER = "Bot Ofertas — Token do Webhook"


def garantir_credenciais(estado: dict) -> dict:
    """Cria as credenciais que faltam e devolve {nome: id}.

    A API pública do n8n não lista credenciais, então a idempotência vem do
    arquivo de estado local. Se você apagar as credenciais no n8n, apague
    também n8n/.n8n_state.json para que sejam recriadas.
    """
    ids = dict(estado.get("credenciais", {}))

    token_tg = (os.getenv("TOKEN_TELEGRAM") or "").strip()
    if CRED_TELEGRAM not in ids:
        if not token_tg:
            print(f"  ⚠️  TOKEN_TELEGRAM ausente no .env — credencial "
                  f"'{CRED_TELEGRAM}' não criada (crie à mão no n8n).")
        else:
            criada = _chamar("POST", "/credentials", {
                "name": CRED_TELEGRAM,
                "type": "telegramApi",
                "data": {"accessToken": token_tg},
            })
            ids[CRED_TELEGRAM] = criada.get("id", "")
            print(f"  ✅ Credencial criada: {CRED_TELEGRAM}")
    else:
        print(f"  ↩️  Credencial já existente: {CRED_TELEGRAM}")

    segredo = (os.getenv("N8N_TOKEN") or "").strip()
    if CRED_HEADER not in ids:
        if not segredo:
            print(f"  ⚠️  N8N_TOKEN ausente no .env — credencial "
                  f"'{CRED_HEADER}' não criada. O webhook ficaria SEM "
                  f"autenticação; defina N8N_TOKEN e rode de novo.")
        else:
            criada = _chamar("POST", "/credentials", {
                "name": CRED_HEADER,
                "type": "httpHeaderAuth",
                "data": {"name": "X-Bot-Token", "value": segredo},
            })
            ids[CRED_HEADER] = criada.get("id", "")
            print(f"  ✅ Credencial criada: {CRED_HEADER}")
    else:
        print(f"  ↩️  Credencial já existente: {CRED_HEADER}")

    estado["credenciais"] = ids
    return ids


# ── Preenchimento de CONFIG nos nós Code ─────────────────────────────────────

def _valores_config() -> dict[str, str]:
    """Valores que substituem os placeholders vazios dentro dos nós Code."""
    admin = (os.getenv("ADMIN_CHAT_ID") or "").strip()
    if not admin:
        # ADMIN_IDS já existe no projeto (lista separada por vírgula usada
        # pelos comandos /status e /stats do bot). O primeiro id serve.
        admin = (os.getenv("ADMIN_IDS") or "").split(",")[0].strip()
    return {
        "admin_chat_id": admin,
        "canal": (os.getenv("CANAL_GERAL") or "@ofertaseletronics").strip(),
        "api_bot": (os.getenv("BOT_API_URL") or "").strip(),
    }


def preencher_config(js: str, valores: dict[str, str]) -> str:
    """Troca `campo: ''` por `campo: 'valor'` nos objetos CONFIG.

    Só mexe em campo com valor VAZIO — assim uma edição feita à mão no n8n
    e reimportada nunca é sobrescrita por um .env incompleto.
    """
    for campo, valor in valores.items():
        if not valor:
            continue
        js = re.sub(
            rf"({re.escape(campo)}\s*:\s*)''",
            lambda m, v=valor: f"{m.group(1)}'{v}'",
            js,
        )
    return js


def aplicar_janela(js: str) -> str:
    """Sincroniza a janela de operação do PC dentro dos nós Code.

    Ao contrário de `preencher_config`, aqui o valor é substituído mesmo
    quando já existe — e de propósito. `silencio_de`/`silencio_ate` não são
    uma escolha feita na interface do n8n: são o reflexo de HORA_LIGAR e
    HORA_DESLIGAR do `.env`, os mesmos horários que geram as tarefas do
    Agendador do Windows. Se o Daniel mudar o horário no `.env` e o n8n
    ficasse com a cópia antiga, o watchdog voltaria a alertar "bot caiu"
    todo dia no horário em que o desligamento é planejado — o ruído diário
    que a janela de silêncio existe para eliminar.

    Os JSON versionados já trazem os valores padrão preenchidos (e não
    vazios) para que importar o arquivo à mão pela interface também produza
    um watchdog correto, sem depender do instalador.
    """
    try:
        from core import janela  # noqa: PLC0415

        ag = janela.agenda()
    except ImportError:
        # Sem o módulo, os valores já presentes no JSON (os padrões) estão
        # corretos — deixar como está é melhor que abortar a importação.
        return js
    troca = {
        "silencio_de": ag["desligar"],
        "silencio_ate": ag["ligar"],
    }
    for campo, valor in troca.items():
        js = re.sub(
            rf"({re.escape(campo)}\s*:\s*)'[^']*'",
            lambda m, v=valor: f"{m.group(1)}'{v}'",
            js,
        )
    js = re.sub(
        r"(tolerancia_religar_min\s*:\s*)\d+",
        lambda m: f"{m.group(1)}{ag['tolerancia_religar_min']}",
        js,
    )
    return js


def preparar_workflow(wf: dict, cred_ids: dict, valores: dict) -> dict:
    """Aplica credenciais e CONFIG, e remove campos que a API rejeita."""
    for node in wf.get("nodes", []):
        js = node.get("parameters", {}).get("jsCode")
        if js:
            node["parameters"]["jsCode"] = aplicar_janela(preencher_config(js, valores))
        creds = node.get("credentials") or {}
        for tipo, ref in creds.items():
            nome = ref.get("name", "")
            if nome in cred_ids and cred_ids[nome]:
                ref["id"] = cred_ids[nome]
        if creds:
            node["credentials"] = creds
    # A API pública aceita só estes campos na criação; `tags`, `active` e
    # `id` fazem a requisição ser recusada com 400.
    return {k: wf[k] for k in ("name", "nodes", "connections", "settings") if k in wf}


# ── Configuração assistida do .env ───────────────────────────────────────────

ENV_PATH = os.path.join(BASE, ".env")


ENV_EXEMPLO_PATH = os.path.join(BASE, ".env.example")


def _ler_env_bruto() -> list[str]:
    """Linhas do .env. Num projeto recém-clonado ele não existe ainda.

    Nesse caso partimos do .env.example em vez de um arquivo vazio: gravar
    só as chaves geradas produziria um .env de 4 linhas, sem TOKEN_TELEGRAM
    nem CANAL_GERAL e sem nenhum dos comentários que explicam cada campo —
    ou seja, um bot que nem sobe, com a documentação perdida. É o mesmo que
    o `cp .env.example .env` do README, feito automaticamente.
    """
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            return f.readlines()
    except FileNotFoundError:
        pass
    try:
        with open(ENV_EXEMPLO_PATH, encoding="utf-8") as f:
            linhas = f.readlines()
        return linhas
    except FileNotFoundError:
        return []


def gravar_no_env(valores: dict[str, str]) -> list[str]:
    """Grava/atualiza chaves no .env preservando comentários e ordem.

    Reescrever o arquivo inteiro a partir das variáveis de ambiente perderia
    os comentários (que explicam cada campo) e reordenaria tudo. Aqui só as
    linhas das chaves passadas mudam; o que não existir é acrescentado no fim.
    """
    linhas = _ler_env_bruto()
    restantes = dict(valores)
    saida, alterados = [], []

    for linha in linhas:
        crua = linha.rstrip("\n")
        if "=" in crua and not crua.lstrip().startswith("#"):
            chave = crua.split("=", 1)[0].strip()
            if chave in restantes:
                saida.append(f"{chave}={restantes.pop(chave)}\n")
                alterados.append(chave)
                continue
        saida.append(linha)

    if restantes:
        if saida and not saida[-1].endswith("\n"):
            saida.append("\n")
        saida.append("\n# ── Adicionado por n8n/setup_n8n.py --configurar ──────────\n")
        for chave, valor in restantes.items():
            saida.append(f"{chave}={valor}\n")
            alterados.append(chave)

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(saida)
    return alterados


def descobrir_chat_id(token_telegram: str) -> list[tuple[str, str]]:
    """Descobre chats que já falaram com o bot, via getUpdates.

    Evita o passo manual de abrir a URL do getUpdates no navegador e caçar o
    `chat.id` no meio do JSON — que é onde essa configuração costuma travar.
    Só enxerga quem mandou mensagem recentemente: se a lista vier vazia, é
    porque ninguém falou com o bot (mande /start para ele e rode de novo).
    """
    if not token_telegram:
        return []
    url = f"https://api.telegram.org/bot{token_telegram}/getUpdates"
    try:
        req = request.Request(url, method="GET")
        with request.urlopen(req, timeout=20) as r:
            dados = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  ⚠️  Não consegui consultar o Telegram: {e}")
        return []
    if not dados.get("ok"):
        print(f"  ⚠️  Telegram recusou: {str(dados.get('description'))[:120]}")
        return []

    achados: dict[str, str] = {}
    for upd in dados.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        nome = (chat.get("title") or
                " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or
                chat.get("username") or "sem nome")
        rotulo = f"{nome} ({chat.get('type', '?')})"
        # O mesmo chat aparece em vários updates, e nem todos trazem o nome
        # completo (um update com só `first_name` chegando depois de um com
        # nome e sobrenome sobrescreveria o rótulo melhor). Fica o mais
        # informativo, para o Daniel reconhecer qual chat é o dele.
        anterior = achados.get(str(cid), "")
        if len(rotulo) > len(anterior):
            achados[str(cid)] = rotulo
    return list(achados.items())


def configurar() -> int:
    """Preenche o que falta no .env, sem sobrescrever o que já existe."""
    import secrets  # noqa: PLC0415

    print(f"\n📝 Configurando {ENV_PATH}")
    if not os.path.exists(ENV_PATH):
        print(f"   (não existe ainda — partindo de {os.path.basename(ENV_EXEMPLO_PATH)},")
        print("    para não perder os comentários nem os outros campos)")
    print()
    novos: dict[str, str] = {}

    if not (os.getenv("N8N_API_URL") or "").strip():
        novos["N8N_API_URL"] = "http://localhost:5678"
        print("  N8N_API_URL    → http://localhost:5678 (padrão do n8n local)")
    else:
        print(f"  N8N_API_URL    já definido: {_api_url()}")

    if not (os.getenv("N8N_TOKEN") or "").strip():
        novos["N8N_TOKEN"] = secrets.token_urlsafe(32)
        print("  N8N_TOKEN      → gerado (32 bytes aleatórios; não vai aparecer aqui)")
    else:
        print("  N8N_TOKEN      já definido")

    admin = (os.getenv("ADMIN_CHAT_ID") or "").strip() or \
            (os.getenv("ADMIN_IDS") or "").split(",")[0].strip()
    if admin:
        print(f"  ADMIN_CHAT_ID  já definido: {admin}")
    else:
        print("  ADMIN_CHAT_ID  ausente — perguntando ao Telegram quem falou com o bot…")
        chats = descobrir_chat_id((os.getenv("TOKEN_TELEGRAM") or "").strip())
        if len(chats) == 1:
            cid, nome = chats[0]
            novos["ADMIN_CHAT_ID"] = cid
            print(f"                 → {cid}  ({nome})")
        elif len(chats) > 1:
            print("                 vários chats encontrados:")
            for cid, nome in chats:
                print(f"                   {cid}  {nome}")
            print("                 escolha o SEU e coloque em ADMIN_CHAT_ID no .env")
        else:
            print("                 nenhum chat encontrado. Mande /start para o seu")
            print("                 bot no Telegram e rode este comando de novo.")

    if not _api_key():
        print("\n  N8N_API_KEY    ausente — este é o único que não dá para gerar daqui.")
        print("                 Abra o n8n → Settings → n8n API → Create an API key,")
        print("                 e cole o valor em N8N_API_KEY no .env.")

    if novos:
        alterados = gravar_no_env(novos)
        print(f"\n✅ .env atualizado: {', '.join(alterados)}")
    else:
        print("\n✅ Nada a mudar no .env.")

    # ADMIN_CHAT_ID entra nesta conta. Ele ficava de fora, e o resultado era
    # a pior combinação possível: o campo vazio, o resumo dizendo que só
    # faltava a API key, e o `--importar` completando sem erro. Os workflows
    # sobem, ficam ativos, e nenhum alerta chega a ninguém — incluindo o
    # "o PC nao religou", que é justamente o que sustenta a publicação
    # diária. Uma configuração incompleta que se apresenta como completa
    # custa mais que uma que falha na cara.
    #
    # O valor efetivo considera o que ACABOU de ser gravado: `os.getenv`
    # ainda reflete o ambiente carregado no import e não enxerga o .env
    # novo, então checar só o ambiente reportaria como ausente um campo
    # preenchido dois segundos antes.
    def _efetivo(chave: str) -> str:
        valor = (novos.get(chave) or os.getenv(chave) or "").strip()
        # Um placeholder do .env.example é tão inútil quanto vazio, e pior:
        # passa por preenchido aqui e só falha lá na frente, como um erro
        # obscuro da API do Telegram.
        if valor.lower().startswith(("cole_aqui", "cole-aqui", "seu_", "sua_")):
            return ""
        return valor

    admin_final = _efetivo("ADMIN_CHAT_ID") or admin
    faltando = [c for c in ("N8N_API_KEY", "TOKEN_TELEGRAM") if not _efetivo(c)]
    if not admin_final:
        faltando.append("ADMIN_CHAT_ID")

    if faltando:
        print(f"⚠️  Ainda falta preencher: {', '.join(faltando)}")
        if "ADMIN_CHAT_ID" in faltando:
            print("     Sem ADMIN_CHAT_ID os workflows importam e ativam normalmente,")
            print("     mas NENHUM alerta sai — nem o 'o PC nao religou'. Mande /start")
            print("     para o seu bot no Telegram e rode --configurar de novo.")
        return 1
    print("\nPróximo passo:  python n8n/setup_n8n.py --importar")
    return 0


# ── Operações ────────────────────────────────────────────────────────────────

def listar_workflows() -> list[dict]:
    resposta = _chamar("GET", "/workflows?limit=250")
    return resposta.get("data", resposta if isinstance(resposta, list) else [])


def arquivos_workflow() -> list[str]:
    if not os.path.isdir(DIR_WORKFLOWS):
        raise N8nErro(f"pasta não encontrada: {DIR_WORKFLOWS}")
    return sorted(
        os.path.join(DIR_WORKFLOWS, f)
        for f in os.listdir(DIR_WORKFLOWS) if f.endswith(".json")
    )


def importar(ativar: bool = True) -> int:
    estado = _ler_estado()
    print(f"\n🔌 n8n em {_api_url()}")
    existentes = {w["name"]: w for w in listar_workflows()}
    print(f"   {len(existentes)} workflow(s) já na instância\n")

    print("🔑 Credenciais")
    cred_ids = garantir_credenciais(estado)
    valores = _valores_config()
    if not valores["admin_chat_id"]:
        print("  ⚠️  ADMIN_CHAT_ID vazio — os alertas não terão para onde ir. "
              "Descubra o seu id mandando /start pro bot e preencha no .env.")

    print("\n📦 Workflows")
    falhas = 0
    for caminho in arquivos_workflow():
        with open(caminho, encoding="utf-8") as f:
            wf = json.load(f)
        nome = wf["name"]
        corpo = preparar_workflow(wf, cred_ids, valores)
        try:
            if nome in existentes:
                wid = existentes[nome]["id"]
                _chamar("PUT", f"/workflows/{wid}", corpo)
                print(f"  ♻️  Atualizado: {nome}")
            else:
                criado = _chamar("POST", "/workflows", corpo)
                wid = criado.get("id", "")
                print(f"  ✅ Criado:     {nome}")
            if ativar and wid:
                try:
                    _chamar("POST", f"/workflows/{wid}/activate")
                    print(f"      ▶️  ativado")
                except N8nErro as e:
                    # Ativar exige credencial válida em todo nó de trigger —
                    # a mensagem do n8n diz qual falta, então é repassada.
                    print(f"      ⚠️  não ativou: {e}")
        except N8nErro as e:
            falhas += 1
            print(f"  ❌ {nome}: {e}")

    _gravar_estado(estado)

    base_webhook = _api_url() + "/webhook/bot-ofertas"
    print("\n" + "─" * 62)
    print("Cole no .env do bot (D:\\bot_ofertas\\.env):")
    print(f"    N8N_WEBHOOK_URL={base_webhook}")
    print(f"    N8N_TOKEN={'(o mesmo que você já definiu)' if os.getenv('N8N_TOKEN') else '<defina um segredo>'}")
    print(f"    N8N_ATIVO=1")
    print("Depois reinicie o bot pelo processo PAI:  python -u startup.py")
    print("E teste o caminho inteiro com:            python -m integrations.n8n")
    print("─" * 62)
    return falhas


def testar() -> int:
    print(f"🔌 API:   {_api_url()}")
    print(f"🔑 Chave: {'definida' if _api_key() else '❌ AUSENTE (N8N_API_KEY)'}")
    if not _api_key():
        return 1
    workflows = listar_workflows()
    print(f"✅ Conexão OK — {len(workflows)} workflow(s) na instância.")
    valores = _valores_config()
    for campo, valor in valores.items():
        print(f"   {campo:14} = {valor or '(vazio)'}")
    print(f"   TOKEN_TELEGRAM = {'definido' if os.getenv('TOKEN_TELEGRAM') else '(vazio)'}")
    print(f"   N8N_TOKEN      = {'definido' if os.getenv('N8N_TOKEN') else '(vazio)'}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Instala os workflows do Bot Ofertas no n8n")
    p.add_argument("--importar", action="store_true", help="cria/atualiza e ativa os workflows")
    p.add_argument("--sem-ativar", action="store_true", help="importa sem ativar")
    p.add_argument("--listar", action="store_true", help="lista o que já existe na instância")
    p.add_argument("--testar", action="store_true", help="só confere conexão e configuração")
    p.add_argument("--configurar", action="store_true",
                   help="preenche o .env (gera N8N_TOKEN, descobre ADMIN_CHAT_ID)")
    args = p.parse_args()

    try:
        if args.configurar:
            return configurar()
        if args.listar:
            for w in listar_workflows():
                marca = "▶️ " if w.get("active") else "⏸️ "
                print(f"{marca} {w.get('name')}  (id={w.get('id')})")
            return 0
        if args.importar:
            return 1 if importar(ativar=not args.sem_ativar) else 0
        return testar()
    except N8nErro as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
