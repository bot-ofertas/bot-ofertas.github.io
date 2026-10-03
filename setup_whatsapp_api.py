# -*- coding: utf-8 -*-
"""
SETUP DA WHATSAPP API (Evolution API)
=====================================
Guia interativo para conectar o bot à Evolution API:

  1. Sobe o container Docker (evolution-api)
  2. Cria a instância "botofertas"
  3. Exibe o QR Code para escanear no celular
  4. Lista grupos do usuário e ajuda a preencher o WHATSAPP_GROUP_ID
  5. Testa envio no grupo

Uso:
    python setup_whatsapp_api.py [passo]
    passo: start | qr | groups | test  (padrão: guiado interativo)

O passo `test` manda uma mensagem DE VERDADE no grupo e por isso pede
confirmação digitada antes (Regra 5 — nada sai no WhatsApp do Daniel sem ele
mandar). Os outros passos não publicam nada.
"""
import base64
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE, ".env")

# Ler o `.env` ANTES de olhar o ambiente. Sem esta linha o script era
# inutilizavel: a chave estava no `.env` (e o `docker/evolution.yml`, o
# `rastreador.py` e o `startup.py` a encontravam la), mas aqui `os.getenv`
# olhava so o ambiente do processo e o script abortava com
# "WHATSAPP_API_KEY nao esta no .env" — dizendo para configurar o que ja
# estava configurado. Reproduzido em 2026-09-18. Todo outro ponto de entrada
# do projeto chama `load_dotenv()`; este era o unico que nao chamava, e e
# justamente o que faz a configuracao inicial.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ENV_PATH)
except ImportError:  # pragma: no cover — ambiente sem python-dotenv
    print("⚠️  python-dotenv não instalado — lendo só as variáveis do ambiente.")
    print("   pip install -r requirements.txt")

API_URL = os.getenv("WHATSAPP_WEBHOOK_URL", "http://localhost:8080")
# Sem valor padrao: o anterior estava escrito aqui, num repositorio publico.
# Uma chave que qualquer um le no GitHub autentica o envio de mensagem pelo
# numero do Daniel (Regra 16) — melhor falhar dizendo o que fazer.
API_KEY = os.getenv("WHATSAPP_API_KEY", "") or os.getenv("EVOLUTION_API_KEY", "")
INSTANCE = os.getenv("WHATSAPP_INSTANCE", "botofertas")

SEM_CHAVE = (
    "❌ WHATSAPP_API_KEY (ou EVOLUTION_API_KEY) nao esta no .env.\n"
    f"   Arquivo esperado: {ENV_PATH}\n"
    '   Gere uma: python -c "import secrets; print(secrets.token_hex(16))"'
)


def exigir_chave() -> bool:
    """Checagem da chave — numa FUNCAO, nao no import.

    Antes era um `raise SystemExit` de modulo: importar este arquivo (um
    teste, o `--help`, qualquer ferramenta) derrubava o interpretador antes
    de a primeira linha de `main()` rodar, e o erro saia sem dizer em que
    arquivo procurar.
    """
    if API_KEY:
        return True
    print(SEM_CHAVE)
    return False


def _req(method: str, path: str, body: dict | None = None, timeout: int = 15):
    """Chamada HTTP a Evolution. Devolve `None` quando nem deu para falar
    com ela — o traceback de `ConnectionError` no meio de um guia passo a
    passo nao diz a quem esta instalando que o container nao subiu.
    """
    try:
        import requests  # type: ignore
    except ImportError:
        print("❌ Falta a biblioteca `requests`: pip install -r requirements.txt")
        return None
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    fn = getattr(requests, method.lower())
    kwargs = {"headers": headers, "timeout": timeout}
    if body is not None:
        kwargs["json"] = body
    try:
        return fn(f"{API_URL}{path}", **kwargs)
    except Exception as e:
        print(f"❌ Não consegui falar com a Evolution API em {API_URL}")
        print(f"   {type(e).__name__}: {str(e)[:200]}")
        print("   O container está de pé? python setup_whatsapp_api.py start")
        return None


def start_docker() -> bool:
    print("=" * 60)
    print("  Passo 1/4 — Subindo container da Evolution API")
    print("=" * 60)
    yml = os.path.join(BASE, "docker", "evolution.yml")
    if not os.path.exists(yml):
        print(f"❌ {yml} não encontrado")
        return False
    # `docker compose -f docker/evolution.yml` procura o `.env` no diretorio
    # do ARQUIVO compose (`docker/`), nao no cwd — entao a chave que mora no
    # `.env` da raiz nunca chegava ao `${EVOLUTION_API_KEY:?}` e o compose
    # recusava subir com "defina EVOLUTION_API_KEY no .env", apontando para o
    # arquivo em que a chave ja estava. Verificado em 2026-09-18 com
    # `docker compose config`. O `--env-file` resolve, e passar a chave
    # tambem pelo ambiente do processo cobre quem exporta a variavel em vez
    # de escrever no arquivo.
    cmd = ["docker", "compose"]
    if os.path.exists(ENV_PATH):
        cmd += ["--env-file", ENV_PATH]
    cmd += ["-f", yml, "up", "-d"]

    ambiente = dict(os.environ)
    # O compose le EVOLUTION_API_KEY; o resto do projeto usa WHATSAPP_API_KEY.
    # Quem definiu so a segunda nao deve travar por causa do nome.
    ambiente.setdefault("EVOLUTION_API_KEY", API_KEY)

    try:
        subprocess.run(cmd, check=True, cwd=BASE, env=ambiente)
    except FileNotFoundError:
        print("❌ Docker não instalado. Instale Docker Desktop e tente de novo.")
        return False
    except subprocess.CalledProcessError as e:
        print(f"❌ Falha ao subir container: {e}")
        print(f"   Confira EVOLUTION_API_KEY em {ENV_PATH}")
        return False
    print("✅ Container em execução em http://localhost:8080")

    # Aguarda API responder
    print("⏳ Aguardando API responder...")
    for _ in range(30):
        try:
            import requests  # type: ignore
            r = requests.get(f"{API_URL}/", timeout=2)
            if r.status_code < 500:
                print("✅ API respondeu")
                return True
        except Exception:
            pass  # container ainda subindo — e por isso que existe o laco
        time.sleep(2)
    print("⚠️  API não respondeu em 60s — continue manualmente")
    return False


def _apikey_da_resposta(d: dict) -> str:
    """Tira a apikey do `/instance/create` aceitando as DUAS formas.

    A v1 devolve `{"hash": {"apikey": "..."}}` e a v2 devolve
    `{"hash": "..."}` — uma string. O codigo antigo fazia
    `d.get("hash", {}).get("apikey")`, que contra a v2 levanta
    `AttributeError: 'str' object has no attribute 'get'` e derruba o passo
    2 do guia logo depois de a instancia ter sido criada com sucesso
    (reproduzido em 2026-09-18). O `docker/evolution.yml` fixa a v2.1.1,
    entao a forma que quebrava era justamente a que o projeto usa.
    """
    h = d.get("hash")
    if isinstance(h, dict):
        h = h.get("apikey")
    return h if isinstance(h, str) else ""


def criar_instancia() -> bool:
    print("=" * 60)
    print("  Passo 2/4 — Criando instância botofertas")
    print("=" * 60)
    r = _req("POST", "/instance/create", {
        "instanceName": INSTANCE,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
    })
    if r is None:
        return False
    if r.status_code in (200, 201):
        try:
            d = r.json()
        except ValueError:
            d = {}
        print(f"✅ Instância '{INSTANCE}' criada")
        nova = _apikey_da_resposta(d)
        # So imprime a chave quando ela e NOVA para o operador. Ecoar a que
        # ja esta no .env nao ajuda ninguem e deixa a chave que envia
        # mensagem pelo numero do Daniel no scrollback (Regra 16).
        if nova and nova != API_KEY:
            print(f"⚠️  apikey da instância: {nova}")
            print("   É diferente da que está no .env — atualize WHATSAPP_API_KEY.")
        return True
    if r.status_code == 403 and "already" in r.text.lower():
        print(f"ℹ️  Instância '{INSTANCE}' já existe — usando ela")
        return True
    print(f"❌ Erro {r.status_code}: {r.text[:300]}")
    return False


def mostrar_qr() -> bool:
    print("=" * 60)
    print("  Passo 3/4 — QR Code para escanear")
    print("=" * 60)
    r = _req("GET", f"/instance/connect/{INSTANCE}")
    if r is None:
        return False
    if r.status_code != 200:
        print(f"❌ Erro {r.status_code}: {r.text[:200]}")
        return False
    try:
        d = r.json()
    except ValueError:
        print(f"❌ Resposta não-JSON de /instance/connect: {r.text[:200]}")
        return False
    qr = d.get("qrcode")
    b64 = d.get("base64") or (qr.get("base64", "") if isinstance(qr, dict) else "")
    if not b64:
        pairing = d.get("pairingCode") or d.get("code")
        if pairing:
            print(f"ℹ️  Código de pareamento: {pairing}")
            print("   No celular: WhatsApp > Aparelhos conectados > Conectar > Usar código")
        else:
            print("⚠️  Sem QR nem código — talvez já esteja conectado")
        return True

    # Salva o QR como imagem
    qr_path = os.path.join(BASE, "data", "qr_evolution.png")
    os.makedirs(os.path.dirname(qr_path), exist_ok=True)
    with open(qr_path, "wb") as f:
        f.write(base64.b64decode(b64.split(",")[-1]))
    print(f"✅ QR salvo em: {qr_path}")
    print("   Abra o arquivo e escaneie no celular:")
    print("   WhatsApp > ⋮ > Aparelhos conectados > Conectar um aparelho")
    print("\n⏳ Aguardando conexão...")
    for i in range(60):
        time.sleep(2)
        try:
            resp = _req("GET", f"/instance/connectionState/{INSTANCE}")
            if resp is None:
                continue
            state = resp.json().get("instance", {}).get("state", "")
            if state == "open":
                print("✅ CONECTADO! WhatsApp vinculado à API.")
                return True
        except Exception:
            pass  # instancia ainda negociando a sessao — o laco tenta de novo
    print("⚠️  Timeout aguardando conexão. Rode novamente se necessário.")
    return False


def listar_grupos_e_orientar() -> bool:
    print("=" * 60)
    print("  Passo 4/4 — Grupos disponíveis")
    print("=" * 60)
    r = _req("GET", f"/group/fetchAllGroups/{INSTANCE}?getParticipants=false")
    if r is None:
        return False
    if r.status_code != 200:
        print(f"❌ Erro {r.status_code}: {r.text[:200]}")
        return False
    try:
        data = r.json()
    except ValueError:
        print(f"❌ Resposta não-JSON de /group/fetchAllGroups: {r.text[:200]}")
        return False
    grupos = data if isinstance(data, list) else data.get("groups", [])
    if not grupos:
        print("⚠️  Nenhum grupo encontrado (você precisa participar de algum)")
        return False
    print(f"\nEncontrei {len(grupos)} grupo(s). JIDs para colar no .env:\n")
    for i, g in enumerate(grupos[:30], 1):
        jid = g.get("id", "")
        nome = g.get("subject", "sem nome")
        print(f"  {i:2d}. {nome[:50]:50s}  →  {jid}")
    print("\n📝 No .env, defina:")
    print(f"   WHATSAPP_WEBHOOK_URL={API_URL}")
    print("   WHATSAPP_API_KEY=<a apikey da instância — já está no seu .env>")
    print(f"   WHATSAPP_INSTANCE={INSTANCE}")
    print("   WHATSAPP_GROUP_ID=<cole o JID do grupo acima>")
    return True


MSG_TESTE = (
    "🤖 Teste de conexão do bot com a Evolution API — "
    "se você vê essa mensagem, tudo certo!"
)


def _confirmado_pelo_operador(destino: str) -> bool:
    """Pergunta antes de mandar mensagem DE VERDADE no grupo.

    Este e o unico passo do guia que publica: `enviar_texto()` cai num grupo
    real do Daniel, com gente dentro, e nao existe desfazer no WhatsApp. A
    Regra 5 diz de quem e a conta e de quem e a decisao — entao quem roda
    digita `SIM`, sem atalho de tecla e sem `[s/N]` que se confirma sem
    querer com um Enter.

    Sem terminal interativo (tarefa agendada, CI, `| python`), NAO envia: a
    ausencia de resposta nao e um sim. Para automatizar conscientemente
    existe `--sim`, que e explicito e fica registrado na linha de comando.
    """
    if "--sim" in sys.argv:
        print(f"↪️  --sim recebido: enviando a mensagem de teste para {destino}")
        return True
    if not sys.stdin.isatty():
        print("❌ Sem terminal interativo e sem `--sim`: nada foi enviado.")
        print("   Rode `python setup_whatsapp_api.py test` num terminal,")
        print("   ou `python setup_whatsapp_api.py test --sim` se souber o que faz.")
        return False

    print()
    print("⚠️  Isto manda uma mensagem DE VERDADE, agora, no grupo:")
    print(f"      destino: {destino}")
    print(f"      texto:   {MSG_TESTE}")
    print("   Todo mundo do grupo vai ver, e não dá para apagar para todos depois de 15 min.")
    try:
        resposta = input("   Digite SIM (maiúsculas) para enviar, ou Enter para cancelar: ")
    except (EOFError, KeyboardInterrupt):
        print("\n❌ Cancelado — nada enviado.")
        return False
    if resposta.strip() != "SIM":
        print("❌ Cancelado — nada enviado.")
        return False
    return True


def testar_envio() -> bool:
    from integrations.whatsapp_api import _config, _configurada, enviar_texto, esta_conectada
    if not _configurada():
        print("❌ Falta configuração: WHATSAPP_WEBHOOK_URL, WHATSAPP_API_KEY e")
        print("   WHATSAPP_GROUP_ID (com o JID de verdade, não o exemplo do .env.example).")
        print("   Rode `python setup_whatsapp_api.py groups` para achar o JID.")
        return False
    if not esta_conectada():
        print("❌ Instância não está conectada. Rode `python setup_whatsapp_api.py qr`.")
        return False
    if not _confirmado_pelo_operador(_config()["group_id"]):
        return False
    ok = enviar_texto(MSG_TESTE)
    print("✅ Enviado" if ok else "❌ Falhou — veja data/errors.jsonl")
    return ok


PASSOS = ("all", "start", "qr", "groups", "test")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    passo = args[0] if args else "all"
    if passo not in PASSOS:
        print(f"❌ Passo desconhecido: {passo}")
        print(f"   Uso: python setup_whatsapp_api.py [{' | '.join(PASSOS)}]")
        return 2
    if not exigir_chave():
        return 1

    if passo in ("start", "all"):
        if not start_docker():
            return 1
        criar_instancia()
    if passo in ("qr", "all"):
        mostrar_qr()
    if passo in ("groups", "all"):
        listar_grupos_e_orientar()
    if passo == "test":
        # `all` NAO inclui o `test` de proposito: o guia inteiro roda sem
        # publicar nada, e mandar mensagem no grupo e um passo pedido a
        # parte, de propria vontade.
        return 0 if testar_envio() else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
