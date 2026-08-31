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
    python n8n/setup_n8n.py --preparar    # grava os JSON prontos numa pasta,
                                          # para importar SEM API key

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
import shutil
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

# Ids fixos usados na importação por arquivo (`--preparar-credenciais`).
# 16 caracteres alfanuméricos, o formato que o n8n gera. Fixos para que
# reimportar atualize a mesma credencial em vez de duplicar.
CRED_ID_TELEGRAM = "botOfertasTgrm01"
CRED_ID_HEADER = "botOfertasHdr001"


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

    # Gravação atômica, com uma cópia da versão anterior.
    #
    # O `.env` é o arquivo mais crítico do projeto: sem ele o bot não sobe e
    # o TOKEN_TELEGRAM precisa ser gerado de novo no BotFather. Abrir o
    # próprio arquivo em modo "w" o trunca ANTES de escrever — uma queda de
    # energia, um Ctrl+C ou um disco cheio no meio do `writelines` deixavam
    # o `.env` pela metade ou vazio, e o sintoma seria o bot não subir mais.
    # Escrever ao lado e renomear por cima é atômico (os.replace vale no
    # Windows também), então ou fica o arquivo velho ou o novo, nunca um
    # pedaço dos dois.
    diretorio = os.path.dirname(ENV_PATH) or "."
    temporario = os.path.join(diretorio, f".env.novo.{os.getpid()}")
    try:
        with open(temporario, "w", encoding="utf-8") as f:
            f.writelines(saida)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(ENV_PATH):
            try:
                shutil.copy2(ENV_PATH, ENV_PATH + ".bak")
            except OSError:
                pass  # a cópia é conforto, não pode impedir a gravação
        os.replace(temporario, ENV_PATH)
    finally:
        if os.path.exists(temporario):
            os.remove(temporario)
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

    # ── Caminho de volta: o n8n falando com o bot ───────────────────────
    # Até aqui a ligação é de mão única — o bot empurra eventos e o n8n só
    # escuta. O workflow 05 (comandos remotos: /status, /pausar, /liberar)
    # precisa do contrário, e fica inerte enquanto BOT_API_URL estiver
    # vazio: importa, ativa, e todo comando morre sem resposta.
    #
    # Quando o n8n roda NA MESMA MÁQUINA, o caminho de volta já existe e é
    # gratuito: `127.0.0.1:8724`, o mesmo endereço onde o healthcheck já
    # escuta. Não abre porta nenhuma para a rede, não muda bind, não
    # aumenta exposição — só deixa de desperdiçar um canal que estava ali.
    #
    # Fora dessa máquina (n8n.cloud, outro host, contêiner Docker) a coisa
    # muda de natureza: exigiria expor a API na rede, e um endpoint que
    # PAUSA a operação não se abre sem decisão explícita do dono. Aí o
    # comando só informa e para.
    api_bot = (os.getenv("BOT_API_URL") or "").strip()
    porta_hc = (os.getenv("HEALTHCHECK_PORTA") or "8724").strip()
    if not api_bot:
        alvo = _api_url()
        local = any(m in alvo for m in ("localhost", "127.0.0.1", "[::1]"))
        segredo = novos.get("N8N_TOKEN") or (os.getenv("N8N_TOKEN") or "").strip()
        if local and segredo:
            novos["BOT_API_URL"] = f"http://127.0.0.1:{porta_hc}"
            print(f"  BOT_API_URL    → http://127.0.0.1:{porta_hc} "
                  "(n8n é local; libera os comandos remotos)")
        elif local and not segredo:
            # Sem N8N_TOKEN o /n8n/comando aceita qualquer chamada de
            # 127.0.0.1. Ainda é local, mas é uma porta de pausa sem
            # tranca — melhor não ligar por conta própria.
            print("  BOT_API_URL    não preenchido: falta N8N_TOKEN para autenticar")
            print("                 os comandos. Rode este comando de novo depois.")
        else:
            print(f"  BOT_API_URL    não preenchido: seu n8n está em {alvo},")
            print("                 fora desta máquina. Os comandos remotos exigiriam")
            print("                 expor o healthcheck na rede (túnel ou 0.0.0.0), e")
            print("                 isso é decisão sua — os outros 4 workflows não")
            print("                 dependem disso. Ver n8n/README.md.")
    else:
        print(f"  BOT_API_URL    já definido: {api_bot}")

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
    # WHATSAPP_GROUP_ID e a chave geral do WhatsApp: vazio = a fila nunca
    # envia. Ficava de fora desta lista, entao um `.env` recem-criado saia
    # daqui "pronto" com o WhatsApp desligado em silencio — e o /health
    # ainda dizia OK enquanto o grupo nao recebia nada.
    if not _efetivo("WHATSAPP_GROUP_ID"):
        faltando.append("WHATSAPP_GROUP_ID")

    if faltando:
        print(f"⚠️  Ainda falta preencher: {', '.join(faltando)}")
        if "WHATSAPP_GROUP_ID" in faltando:
            print("     Sem WHATSAPP_GROUP_ID o Telegram publica normalmente, mas o")
            print("     grupo do WhatsApp nao recebe NADA (a fila para em silencio).")
            print("     Confira tambem WHATSAPP_GROUP_NAME: e por ele que a automacao")
            print("     acha a conversa no WhatsApp Desktop, e precisa bater exatamente.")
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


def preparar_credenciais(destino: str) -> int:
    """Gera o arquivo que a CLI do n8n importa como credenciais.

    Sem isso sobra a parte mais chata da instalação manual: abrir o n8n,
    criar "Bot Ofertas — Telegram", colar o token, criar outra credencial de
    Header Auth, acertar o nome do header exatamente como `X-Bot-Token` —
    e um erro de digitação aí devolve 403 no webhook sem dizer por quê.

    O arquivo sai com os segredos EM CLARO, porque é assim que o
    `n8n import:credentials` os recebe (ele cifra na hora de gravar, com a
    chave da própria instância). Por isso ele nasce numa pasta temporária e
    quem chama tem obrigação de apagá-lo logo depois — nunca no repositório,
    nunca perto do git.
    """
    token_tg = (os.getenv("TOKEN_TELEGRAM") or "").strip()
    segredo = (os.getenv("N8N_TOKEN") or "").strip()

    faltam = [n for n, v in (("TOKEN_TELEGRAM", token_tg), ("N8N_TOKEN", segredo)) if not v]
    if faltam:
        print(f"❌ Não dá para gerar as credenciais sem {', '.join(faltam)} no .env.",
              file=sys.stderr)
        return 1

    # O `id` NÃO é opcional, ao contrário do que o exemplo da documentação
    # sugere: sem ele o import morre com
    # "SQLITE_CONSTRAINT: NOT NULL constraint failed: credentials_entity.id"
    # (reproduzido contra o n8n 2.35.7). E ele é FIXO de propósito: rodar o
    # instalador de novo atualiza a mesma credencial em vez de encher o
    # cofre de duplicatas com o mesmo nome, que é o tipo de bagunça que
    # depois faz o workflow apontar para a credencial errada.
    creds = [
        {"id": CRED_ID_TELEGRAM, "name": CRED_TELEGRAM, "type": "telegramApi",
         "data": {"accessToken": token_tg}},
        # O nome do header tem que bater com o que `integrations/n8n.py`
        # envia. É literal nos dois lados de propósito: um valor derivado
        # de variável aqui seria mais uma coisa para sair de sincronia.
        {"id": CRED_ID_HEADER, "name": CRED_HEADER, "type": "httpHeaderAuth",
         "data": {"name": "X-Bot-Token", "value": segredo}},
    ]

    os.makedirs(os.path.dirname(os.path.abspath(destino)) or ".", exist_ok=True)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(destino, 0o600)
    except OSError:
        # Windows ignora o modo POSIX; o arquivo já nasce numa pasta
        # temporária do usuário e é apagado em seguida.
        pass

    print(f"✅ Credenciais geradas em {destino} ({len(creds)} itens).")
    print("   Contém segredos em claro — apague depois de importar:")
    print(f'   n8n import:credentials --input="{destino}"')
    return 0


def preparar_para_importar(destino: str = "") -> int:
    """Grava os workflows já preenchidos numa pasta, sem tocar na rede.

    Existe porque a API pública do n8n exige uma API key, e conseguir essa
    chave depende de achar a tela certa numa interface que muda de versão
    para versão — foi exatamente onde a instalação travou. O n8n instalado
    localmente importa por linha de comando (`n8n import:workflow`) sem
    chave nenhuma, e a interface aceita "Import from File". Os dois querem
    a mesma coisa: os JSON com `admin_chat_id`, `canal` e a janela de
    operação já preenchidos.

    O que sai daqui é idêntico ao que o `--importar` enviaria — mesma
    função `preparar_workflow` — só que em arquivo em vez de HTTP.
    """
    destino = destino or os.path.join(BASE, "n8n", "prontos")
    os.makedirs(destino, exist_ok=True)

    valores = _valores_config()
    print(f"\n📦 Preparando workflows em {destino}\n")
    for campo, valor in valores.items():
        print(f"   {campo:14} = {valor or '(vazio)'}")
    if not valores.get("admin_chat_id"):
        print("\n   ⚠️  admin_chat_id vazio: os workflows importam e ativam,")
        print("       mas NENHUM alerta sai. Mande /start para o seu bot e")
        print("       rode `--configurar` antes deste comando.")

    gravados = []
    for nome in sorted(os.listdir(DIR_WORKFLOWS)):
        if not nome.endswith(".json"):
            continue
        with open(os.path.join(DIR_WORKFLOWS, nome), encoding="utf-8") as f:
            wf = json.load(f)
        # Os ids FIXOS das credenciais, os mesmos que
        # `--preparar-credenciais` grava. Sem isso os nós ficam apontando
        # para o placeholder `REPLACE_TELEGRAM_CRED`: a importação diz
        # "Successfully imported", o workflow aparece bonito na tela, e só
        # na hora de publicar é que o nó do Telegram falha por credencial
        # inexistente. O n8n resolve credencial por ID, não pelo nome —
        # bater só o nome não basta (verificado contra o n8n 2.35.7).
        pronto = preparar_workflow(
            wf,
            {CRED_TELEGRAM: CRED_ID_TELEGRAM, CRED_HEADER: CRED_ID_HEADER},
            valores,
        )
        caminho = os.path.join(destino, nome)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(pronto, f, ensure_ascii=False, indent=2)
        gravados.append(nome)
        print(f"   ✅ {nome}")

    print(f"\n{len(gravados)} workflow(s) prontos. Duas formas de importar, "
          "nenhuma precisa de API key:\n")
    print("  1) Linha de comando do próprio n8n (importa os 5 de uma vez):")
    print(f'     n8n import:workflow --separate --input="{destino}"\n')
    print("  2) Pela interface, um a um:")
    print("     Workflows → ⋯ (canto superior direito) → Import from File\n")
    print("Depois de importar, faça DUAS coisas na interface — elas são o")
    print("que a API faria sozinha:")
    print("  · crie as credenciais 'Bot Ofertas — Telegram' (o token do")
    print("    BotFather) e 'Bot Ofertas — Token do Webhook' (Header Auth,")
    print("    nome do header X-Bot-Token, valor = N8N_TOKEN do seu .env);")
    print("  · ative cada workflow no botão Active.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Instala os workflows do Bot Ofertas no n8n")
    p.add_argument("--importar", action="store_true", help="cria/atualiza e ativa os workflows")
    p.add_argument("--sem-ativar", action="store_true", help="importa sem ativar")
    p.add_argument("--listar", action="store_true", help="lista o que já existe na instância")
    p.add_argument("--testar", action="store_true", help="só confere conexão e configuração")
    p.add_argument("--configurar", action="store_true",
                   help="preenche o .env (gera N8N_TOKEN, descobre ADMIN_CHAT_ID)")
    p.add_argument("--preparar", nargs="?", const="", metavar="PASTA",
                   help="grava os workflows preenchidos numa pasta, para importar "
                        "sem API key (n8n import:workflow ou Import from File)")
    p.add_argument("--preparar-credenciais", metavar="ARQUIVO",
                   help="gera o JSON de credenciais para `n8n import:credentials` "
                        "(contem segredos em claro: apague depois de importar)")
    args = p.parse_args()

    try:
        if args.configurar:
            return configurar()
        if args.preparar_credenciais:
            return preparar_credenciais(args.preparar_credenciais)
        if args.preparar is not None:
            return preparar_para_importar(args.preparar)
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
