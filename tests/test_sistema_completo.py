# -*- coding: utf-8 -*-
"""
TESTE DE SISTEMA — bot e n8n montados, conversando de verdade.

Diferente de tests/test_n8n_integracao.py (que testa funções isoladas), aqui
o caminho inteiro roda junto:

  * um "n8n" local autentica como o nó Webhook (Header Auth) e EXECUTA os
    Code nodes reais dos workflows versionados, com `$getWorkflowStaticData`
    persistida entre chamadas — como no n8n de verdade;
  * os workflows passam antes por `n8n/setup_n8n.preparar_workflow`, ou seja,
    o teste roda o estado em que o instalador os deixa, não o JSON cru;
  * o healthcheck sobe e responde de verdade, e o comando que o n8n manda
    (`/status`, `pausar`, `retomar`, `quarentena_liberar`) atravessa o
    workflow 05 e chega no bot pela API autenticada.

Precisa do Node (só para executar os Code nodes). Sem Node, o teste é
pulado com aviso em vez de falhar.

Rodar:
    python tests/test_sistema_completo.py
"""
import atexit, json, os, shutil, subprocess, sys, tempfile, threading, time
import urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RUNNER_FONTE = r"""// Executa um Code node do n8n fora do n8n, simulando o runtime dele:
//   $json, $getWorkflowStaticData('global') e $('Nó').first().json
// A static data é persistida em arquivo entre chamadas, como o n8n faz.
const fs = require('fs');
const [, , wfFile, nodeName, inputFile, storeFile, ctxFile] = process.argv;

const wf = JSON.parse(fs.readFileSync(wfFile, 'utf8'));
const node = wf.nodes.find(n => n.name === nodeName);
if (!node) { console.error('nó não encontrado: ' + nodeName); process.exit(2); }

const $json = JSON.parse(fs.readFileSync(inputFile, 'utf8'));
let store = {};
try { store = JSON.parse(fs.readFileSync(storeFile, 'utf8')); } catch (e) {}
let ctx = {};
if (ctxFile && fs.existsSync(ctxFile)) ctx = JSON.parse(fs.readFileSync(ctxFile, 'utf8'));

const $getWorkflowStaticData = () => store;
const $ = (nome) => ({ first: () => ({ json: ctx[nome] || {} }) });

const fn = new Function('$json', '$getWorkflowStaticData', '$', node.parameters.jsCode);
const out = fn($json, $getWorkflowStaticData, $);

fs.writeFileSync(storeFile, JSON.stringify(store, null, 1));
console.log(JSON.stringify(out));
"""

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

if not shutil.which("node"):
    print("⚠️  Node não encontrado — os Code nodes do n8n não podem ser "
          "executados. Teste PULADO (não é falha).")
    sys.exit(0)

E2E = tempfile.mkdtemp(prefix="bot_sistema_")
atexit.register(shutil.rmtree, E2E, True)

# O runner simula o runtime do Code node do n8n ($json,
# $getWorkflowStaticData, $('Nó').first().json).
RUNNER_JS = os.path.join(E2E, "runner.js")
with open(RUNNER_JS, "w", encoding="utf-8") as _f:
    _f.write(RUNNER_FONTE)

TOKEN = "segredo-e2e-do-daniel"
os.environ.update({
    "N8N_WEBHOOK_URL": "http://127.0.0.1:9931/webhook/bot-ofertas",
    "N8N_TOKEN": TOKEN, "N8N_ATIVO": "1",
    "TOKEN_TELEGRAM": "fake:token", "CANAL_GERAL": "@ofertaseletronics",
    "ADMIN_CHAT_ID": "555000111", "HEALTHCHECK_PORTA": "8791",
    "BOT_API_URL": "http://127.0.0.1:8791",
})

sys.path.insert(0, os.path.join(RAIZ, "n8n"))
from setup_n8n import _valores_config, preparar_workflow  # noqa: E402

WF_DIR = os.path.join(E2E, "workflows_instalados")
os.makedirs(WF_DIR, exist_ok=True)
_vals = _valores_config()
for _nome in sorted(os.listdir(os.path.join(RAIZ, "n8n", "workflows"))):
    if not _nome.endswith(".json"):
        continue
    with open(os.path.join(RAIZ, "n8n", "workflows", _nome), encoding="utf-8") as _f:
        _wf = json.load(_f)
    with open(os.path.join(WF_DIR, _nome), "w", encoding="utf-8") as _f:
        json.dump(preparar_workflow(_wf, {}, _vals), _f, ensure_ascii=False)

# A bandeira de pausa é um arquivo em data/ compartilhado pelos 4 processos
# do bot. Se este teste usasse o caminho real e abortasse entre `pausar` e
# `retomar`, deixaria o bot do Daniel PAUSADO — sem ninguém perceber, já que
# a pausa é silenciosa por natureza. Redireciona para a pasta temporária.
from core import pausa as _pausa  # noqa: E402
_pausa.FLAG_PATH = os.path.join(E2E, "pausado.flag")

# Mesmo motivo para o spool: um teste não deve enfileirar eventos no arquivo
# que o bot em produção vai reenviar depois.
from integrations import n8n as _n8n  # noqa: E402
_n8n.SPOOL_PATH = os.path.join(E2E, "n8n_spool.jsonl")

STORE_W1 = os.path.join(E2E, "store_w1.json")
STORE_W2 = os.path.join(E2E, "store_w2.json")
STORE_W3 = os.path.join(E2E, "store_w3.json")
for f in (STORE_W1, STORE_W2, STORE_W3):
    open(f, "w").write("{}")

ok_total, falhas = 0, []

def checar(nome, condicao, detalhe=""):
    global ok_total
    if condicao:
        ok_total += 1
        print(f"   ✅ {nome}" + (f" — {detalhe}" if detalhe else ""))
    else:
        falhas.append(nome)
        print(f"   ❌ {nome}" + (f" — {detalhe}" if detalhe else ""))

def rodar_no(workflow, no, entrada, store, ctx=None):
    """Executa um Code node real, no estado em que o instalador o deixa."""
    ent = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(entrada, ent); ent.close()
    ctx_path = ""
    if ctx:
        c = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(ctx, c); c.close(); ctx_path = c.name
    r = subprocess.run(
        ["node", RUNNER_JS,
         os.path.join(WF_DIR, workflow), no, ent.name, store, ctx_path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"{workflow}/{no}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)[0]["json"]

# ── "n8n" local: valida header e roda o W1 de verdade ───────────────────────
recebidos, alertas = [], []

class N8nFalso(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        # Header Auth do nó Webhook: sem o token certo, 403 antes de tudo.
        if (self.headers.get("X-Bot-Token") or "") != TOKEN:
            self.send_response(403); self.end_headers(); self.wfile.write(b'{"erro":"nao autorizado"}'); return
        corpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        payload = json.loads(corpo)
        recebidos.append({"payload": payload, "assinatura": self.headers.get("X-Bot-Assinatura", "")})
        entrada = {"body": payload, "headers": dict(self.headers)}
        cfg = rodar_no("01-ingestao-e-watchdog.json", "Configuração", entrada, STORE_W1)
        rot = rodar_no("01-ingestao-e-watchdog.json", "Rotear evento", cfg, STORE_W1)
        if rot.get("tem_alerta"):
            alertas.append(rot)
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.end_headers(); self.wfile.write(b'{"ok":true}')

ThreadingHTTPServer.allow_reuse_address = True
srv = ThreadingHTTPServer(("127.0.0.1", 9931), N8nFalso)
threading.Thread(target=srv.serve_forever, daemon=True).start()

print("\n╔══════════════════════════════════════════════════════════════════╗")
print("║  TESTE DE SISTEMA COMPLETO — Bot Ofertas + n8n                   ║")
print("╚══════════════════════════════════════════════════════════════════╝")

# ── 1. Banco limpo + quarentena ─────────────────────────────────────────────
print("\n[1] Banco, quarentena e ciclo de vida do produto")
import core.database as db
tmp = tempfile.mkdtemp(prefix="e2e_")
db._DB_PATH = os.path.join(tmp, "e2e.db"); db._falhas_tbl_checked = False
db.inicializar()

pid = "MLB68674214"
db.claim_produto(pid, "Smart TV 32 Philco")
produto = {
    "id": pid, "titulo": "Smart TV 32'' Philco HD", "preco": 799.0,
    "preco_original": 1299.0, "desconto_pct": 38.5, "categoria": "tvs",
    "canal": "geral", "status": "pendente", "score": 78,
    "foto": "https://http2.mlstatic.com/D_NQ_NP_811742-MLB68674214-I.jpg",
}
db.atualizar_produto(produto)
r1 = db.registrar_falha_publicacao(pid, "foto indisponível", produto["titulo"])
r2 = db.registrar_falha_publicacao(pid, "foto indisponível", produto["titulo"])
r3 = db.registrar_falha_publicacao(pid, "foto indisponível", produto["titulo"])
checar("3 falhas -> quarentena", r3["quarentena"] and not r2["quarentena"],
       f"tentativas {r1['tentativas']}/{r2['tentativas']}/{r3['tentativas']}")
checar("rastreador pula produto em quarentena", db.em_quarentena(pid) is True)
checar("quarentena visível para o operador", len(db.listar_quarentena()) == 1)

# ── 2. Foto em alta resolução ───────────────────────────────────────────────
print("\n[2] Normalização da foto (o bug relatado)")
from core.foto_url import alta_resolucao, variantes
alta = alta_resolucao(produto["foto"])
checar("miniatura -I 1x vira original -O 2X", alta.endswith("-O.jpg") and "2X" in alta, alta[-45:])
checar("cadeia de variantes para o CDN recusar", len(variantes(produto["foto"])) >= 2,
       f"{len(variantes(produto['foto']))} candidatas")

# ── 3. Afiliado e origem por canal ──────────────────────────────────────────
print("\n[3] Afiliado preservado em cada canal")
from urllib.parse import parse_qs, urlsplit
from core.tracking import afiliado_intacto, marcar_origem
base = f"https://www.mercadolivre.com.br/p/{pid}?matt_tool=47114387&matt_source=bot_telegram#polycard_client=x"
for canal, esperado in (("telegram", "bot_telegram"), ("whatsapp", "bot_whatsapp"),
                        ("instagram", "instagram"), ("meta_ads", "meta_ads")):
    link = marcar_origem(base, canal)
    q = parse_qs(urlsplit(link).query)
    checar(f"canal {canal}", q.get("matt_source") == [esperado]
           and q.get("matt_tool") == ["47114387"] and "#" not in link,
           f"matt_source={q.get('matt_source', [''])[0]}, matt_tool preservado")
checar("afiliado_intacto confirma por parse_qs (não substring)",
       afiliado_intacto(marcar_origem(base, "whatsapp"), matt_tool="47114387"))

# ── 4. Bot -> n8n: eventos reais atravessando o W1 ──────────────────────────
print("\n[4] Bot empurra eventos; W1 do n8n processa de verdade")
from integrations import n8n
checar("integração ativa com token", n8n.ativo() and bool(os.environ["N8N_TOKEN"]))

n8n.emitir("heartbeat", {"fila_whatsapp": 2, "erros_10min": 0, "quarentena": 1}, bloqueante=True)
n8n.emitir("oferta_publicada", {"produto_id": pid, "titulo": produto["titulo"],
                                "preco": 799.0, "desconto_pct": 38.5,
                                "link": marcar_origem(base, "telegram")}, bloqueante=True)
n8n.emitir("produto_quarentena", r3, bloqueante=True)
n8n.emitir("rodada_concluida", {"publicados": 4, "erros": 7, "duplicatas": 12,
                                "links_falharam": 1, "duracao_s": 92.4}, bloqueante=True)
time.sleep(0.4)

checar("4 eventos entregues e autenticados", len(recebidos) == 4, f"{len(recebidos)} recebidos")
checar("HMAC do corpo confere no destino",
       all(n8n.conferir_assinatura(json.dumps(r["payload"], ensure_ascii=False).encode(),
                                   r["assinatura"]) for r in recebidos))

store = json.load(open(STORE_W1))
checar("W1 contabilizou a oferta publicada", store.get("ofertas_hoje") == 1)
checar("W1 guardou o heartbeat", bool(store.get("ultimo_heartbeat_ts")))
checar("W1 guardou o resumo da rodada", store.get("ultima_rodada", {}).get("publicados") == 4)

textos = [a["alerta"] for a in alertas]
checar("alerta de quarentena gerado", any("quarentena" in t.lower() for t in textos))
checar("alerta de rodada com muitos erros", any("7 erro" in t for t in textos))
checar("oferta publicada NÃO vira alerta (só contabiliza)", len(alertas) == 2,
       f"{len(alertas)} alertas para 4 eventos")
checar("alerta endereçado ao chat do admin",
       all(a["chat_id"] == "555000111" for a in alertas))

# ── 5. Webhook rejeita quem não tem o token ─────────────────────────────────
print("\n[5] Webhook recusa requisição sem o segredo")
req = urllib.request.Request("http://127.0.0.1:9931/webhook/bot-ofertas",
                             data=b'{"evento":"invasao"}', method="POST")
req.add_header("X-Bot-Token", "token-errado")
try:
    urllib.request.urlopen(req, timeout=5); checar("recusa sem token", False, "aceitou!")
except urllib.error.HTTPError as e:
    checar("recusa sem token", e.code == 403, f"HTTP {e.code}")

# ── 6. Watchdog do n8n percebe o bot morto ──────────────────────────────────
print("\n[6] Watchdog na nuvem detecta queda do PC")
s = json.load(open(STORE_W1))
s["ultimo_heartbeat_ts"] = int((time.time() - 45 * 60) * 1000)   # 45 min atrás
json.dump(s, open(STORE_W1, "w"))
w = rodar_no("01-ingestao-e-watchdog.json", "Checar heartbeat", {}, STORE_W1)
checar("alerta de queda disparado", w.get("alertar") is True, f"idade {w.get('idade_min')} min")
checar("mensagem nomeia o problema", "parou de responder" in (w.get("texto") or ""))
w2 = rodar_no("01-ingestao-e-watchdog.json", "Checar heartbeat", {}, STORE_W1)
checar("não repete o alerta a cada 15 min", w2.get("alertar") is False)
s = json.load(open(STORE_W1)); s["ultimo_heartbeat_ts"] = int(time.time() * 1000)
json.dump(s, open(STORE_W1, "w"))
w3 = rodar_no("01-ingestao-e-watchdog.json", "Checar heartbeat", {}, STORE_W1)
checar("avisa quando o bot volta", w3.get("alertar") is True and "voltou" in w3.get("texto", ""))

# ── 7. Spool: nada se perde com o n8n fora do ar ────────────────────────────
print("\n[7] Queda do n8n não perde evento")
srv.shutdown(); srv.server_close()
antes = n8n.tamanho_spool()
n8n.emitir("rodada_falhou", {"erro": "Timed out", "fonte": "mercadolivre"}, bloqueante=True)
checar("evento guardado no spool", n8n.tamanho_spool() == antes + 1)
srv2 = ThreadingHTTPServer(("127.0.0.1", 9931), N8nFalso)
threading.Thread(target=srv2.serve_forever, daemon=True).start()
checar("reenviado quando o n8n volta", n8n.flush_spool() >= 1 and n8n.tamanho_spool() == 0)
checar("W1 processou o evento reenviado", any("Timed out" in (a["alerta"] or "") for a in alertas))

# ── 8. Healthcheck + API de comandos ────────────────────────────────────────
print("\n[8] Healthcheck e API de comandos do bot")
from core.healthcheck import iniciar_healthcheck
iniciar_healthcheck(com_n8n=False)
time.sleep(0.6)
BASE_API = "http://127.0.0.1:8791"

def get(caminho):
    try:
        with urllib.request.urlopen(BASE_API + caminho, timeout=6) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

st, h = get("/health")
checar("/health responde", st in (200, 503), f"HTTP {st}")
for bloco in ("n8n", "pausa", "quarentena", "ml_token", "telegram", "rastreador"):
    checar(f"/health traz bloco '{bloco}'", bloco in h)
checar("/health mostra n8n ativo", h["n8n"]["ativo"] is True)
checar("/health mostra a quarentena", h["quarentena"]["total"] == 1,
       f"{h['quarentena']['total']} produto(s)")
checar("/health nomeia o componente crítico com falha", "criticos_com_falha" in h)

st, q = get("/quarentena")
checar("/quarentena lista o produto travado", st == 200 and q["itens"][0]["produto_id"] == pid)

st, d = get("/divulgacao?rede=instagram&tipo=grupo")
checar("/divulgacao gera anúncio", st == 200 and len(d["texto"]) > 100)
checar("anúncio traz os DOIS grupos",
       "t.me/ofertaseletronics" in d["texto"] and "chat.whatsapp.com" in d["texto"])
checar("anúncio traz UTM da rede", "utm_source=instagram" in d["texto"])

def post_cmd(corpo, headers):
    dados = json.dumps(corpo).encode()
    req = urllib.request.Request(BASE_API + "/n8n/comando", data=dados, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

st, _ = post_cmd({"comando": "status"}, {"X-Bot-Token": "errado"})
checar("comando com token errado -> 401", st == 401)

# ── 9. n8n -> bot: o W5 traduz o comando do Telegram e chama a API ──────────
print("\n[9] Comando do Telegram atravessa o W5 e chega no bot")
mensagem = {"message": {"text": "/status", "chat": {"id": 555000111}}}
cmd = rodar_no("05-comandos-remotos.json", "Interpretar comando", mensagem, STORE_W1)
checar("W5 autoriza o admin", cmd.get("autorizado") is True)
checar("W5 monta a chamada para a API do bot", cmd.get("chamar") is True,
       f"url={cmd.get('url', '')}")
checar("W5 traduziu /status no comando certo",
       (cmd.get("corpo") or {}).get("comando") == "status")

st, resp = post_cmd(cmd["corpo"], {"X-Bot-Token": TOKEN})
checar("bot executou o comando do n8n", st == 200 and resp["ok"] is True)
res = resp["resultado"]
checar("resposta traz stats, fila e quarentena",
       "stats" in res and "fila_whatsapp" in res and res["quarentena"] == 1)

fmt = rodar_no("05-comandos-remotos.json", "Formatar resposta", resp, STORE_W1,
               ctx={"Interpretar comando": cmd})
checar("W5 formata a resposta para o Telegram", "Status" in fmt.get("resposta", ""))

est = rodar_no("05-comandos-remotos.json", "Interpretar comando",
               {"message": {"text": "/status", "chat": {"id": 999}}}, STORE_W1)
checar("W5 recusa comando de estranho", est.get("autorizado") is False)

print("\n[10] Pausa global e retomada pelo n8n")
st, r = post_cmd({"comando": "pausar", "dados": {"motivo": "teste de sistema"}},
                 {"X-Bot-Token": TOKEN})
from core import pausa
checar("pausar via n8n", st == 200 and pausa.pausado() is True)
st, h2 = get("/health")
checar("/health reflete a pausa", h2["pausa"]["pausado"] is True)
st, r = post_cmd({"comando": "retomar"}, {"X-Bot-Token": TOKEN})
checar("retomar via n8n", pausa.pausado() is False)

st, r = post_cmd({"comando": "quarentena_liberar", "dados": {"produto_id": pid}},
                 {"X-Bot-Token": TOKEN})
checar("liberar quarentena via n8n", r["resultado"]["liberados"] == 1
       and db.em_quarentena(pid) is False)

# ── 11. Workflows de nuvem contra o offers.json REAL do site ────────────────
print("\n[11] Workflows de nuvem rodando sobre o offers.json do site")
offers = json.load(open(os.path.join(RAIZ, "docs", "data", "offers.json"), encoding="utf-8"))
n_produtos = len(offers.get("products", []))
print(f"    (offers.json atual tem {n_produtos} produto(s))")

dig = rodar_no("02-publicacao-reforco.json", "Montar digest", offers, STORE_W2)
if n_produtos:
    checar("W2 monta o digest TOP 3", dig.get("publicar") is True)
    checar("digest leva o grupo do WhatsApp", "chat.whatsapp.com" in dig.get("texto", ""))
else:
    checar("W2 não publica vazio (site sem ofertas agora)",
           dig.get("publicar") is False, dig.get("motivo", ""))

div = rodar_no("03-divulgacao-social.json", "Gerar anúncio", offers, STORE_W3)
checar("W3 gera anúncio pronto", bool(div.get("texto")), f"rede: {div.get('rede')}")
checar("W3 inclui os dois grupos com UTM",
       "t.me/ofertaseletronics" in div["texto"] and "chat.whatsapp.com" in div["texto"]
       and f"utm_source={div['rede']}" in div["texto"])
div2 = rodar_no("03-divulgacao-social.json", "Gerar anúncio", offers, STORE_W3)
checar("W3 alterna a rede a cada rodada", div2.get("rede") != div.get("rede"),
       f"{div.get('rede')} -> {div2.get('rede')}")

runs_falsos = {"workflow_runs": [{"conclusion": "success"}, {"conclusion": "failure"},
                                 {"conclusion": "success"}]}
rel = rodar_no("04-relatorio-diario.json", "Montar relatório", {}, STORE_W1,
               ctx={"Ofertas do site": offers, "Execuções do GitHub Actions": runs_falsos})
checar("W4 monta o relatório diário", "Relatório diário" in rel.get("texto", ""))
checar("W4 põe o problema na PRIMEIRA linha", rel.get("problemas", 0) >= 1
       and "🔴" in rel["texto"].split("\n")[2 if rel["texto"].count("\n") > 2 else 0]
       or "🔴" in rel["texto"])
checar("W4 mostra o placar do GitHub Actions", "✅" in rel["texto"] and "❌" in rel["texto"])

# ── Resultado ───────────────────────────────────────────────────────────────
srv2.shutdown()
print("\n" + "═" * 68)
if falhas:
    print(f"❌ {len(falhas)} verificação(ões) falharam de {ok_total + len(falhas)}:")
    for f in falhas:
        print(f"   • {f}")
    sys.exit(1)
print(f"✅ SISTEMA COMPLETO VALIDADO — {ok_total}/{ok_total} verificações passaram")
print("═" * 68)
