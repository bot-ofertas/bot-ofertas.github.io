# -*- coding: utf-8 -*-
"""
Ciclo diário — liga 08:30 / desliga 02:00.

Três coisas precisam concordar sobre o mesmo horário, ou o ciclo falha em
silêncio: o `core/janela.py` (que gera as tarefas do Windows), o supervisor
que ressuscita o bot, e o watchdog do n8n. Este arquivo testa os três — o
watchdog inclusive **executando o JavaScript real** do workflow com o
relógio simulado, porque é a única forma de provar que ele não vai disparar
"bot caiu" às 02:30 de toda madrugada.

Roda sem Node? A parte JS é pulada com aviso, o resto continua valendo.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# A janela é lida do ambiente; fixar os valores aqui impede que um .env local
# com outro horário faça o teste passar (ou falhar) pelo motivo errado.
os.environ["HORA_LIGAR"] = "08:30"
os.environ["HORA_DESLIGAR"] = "02:00"
os.environ["HORA_VERIFICACAO"] = "01:00"

from core import janela  # noqa: E402

_falhas: list[str] = []
_ok = 0


def checar(nome: str, condicao: bool, detalhe: str = "") -> None:
    global _ok
    if condicao:
        _ok += 1
        print(f"  [OK]   {nome}")
    else:
        _falhas.append(f"{nome} {detalhe}".strip())
        print(f"  [FALHA] {nome} {detalhe}")


def em(hhmm: str, dia: str = "2026-08-29") -> datetime:
    return datetime.strptime(f"{dia} {hhmm}", "%Y-%m-%d %H:%M")


# ── 1. A janela atravessa a meia-noite ───────────────────────────────────
print("\n[1] core/janela.py — janela que cruza a meia-noite")

checar("08:30 (instante de religar) está dentro", janela.dentro_da_janela(em("08:30")))
checar("08:29 (um minuto antes) está fora", not janela.dentro_da_janela(em("08:29")))
checar("12:00 está dentro", janela.dentro_da_janela(em("12:00")))
checar("23:59 está dentro", janela.dentro_da_janela(em("23:59")))
# O caso que a comparação ingênua `inicio <= t < fim` erra: depois da
# meia-noite o relógio é MENOR que os dois extremos, e o horário de pico da
# madrugada seria classificado como "PC desligado".
checar("00:30 (depois da meia-noite) está dentro", janela.dentro_da_janela(em("00:30")))
checar("01:59 ainda está dentro", janela.dentro_da_janela(em("01:59")))
checar("02:00 (instante de desligar) está fora", not janela.dentro_da_janela(em("02:00")))
checar("05:00 (madrugada) está fora", not janela.dentro_da_janela(em("05:00")))
checar("em_silencio é o complemento de dentro_da_janela",
       janela.em_silencio(em("05:00")) and not janela.em_silencio(em("12:00")))

checar("agenda() publica os horários acordados",
       janela.agenda() == {"ligar": "08:30", "desligar": "02:00",
                           "verificacao": "01:00", "tolerancia_religar_min": 45},
       str(janela.agenda()))

# ── 2. Próximos eventos ──────────────────────────────────────────────────
print("\n[2] Próxima religada / próximo desligamento")

checar("às 05:00, religa hoje 08:30",
       janela.proxima_religada(em("05:00")) == em("08:30"))
checar("às 12:00, religa amanhã 08:30",
       janela.proxima_religada(em("12:00")) == em("08:30") + timedelta(days=1))
checar("às 12:00, desliga hoje... na verdade amanhã 02:00",
       janela.proximo_desligamento(em("12:00")) == em("02:00") + timedelta(days=1))
checar("às 01:00, desliga hoje 02:00",
       janela.proximo_desligamento(em("01:00")) == em("02:00"))

checar("09:15 (45min após religar) conta como religada atrasada",
       janela.religada_atrasada(em("09:15")))
checar("08:40 (antes da tolerância) não conta",
       not janela.religada_atrasada(em("08:40")))
checar("15:00 não conta (a manhã já passou; outro problema, outro alerta)",
       not janela.religada_atrasada(em("15:00")))

# ── 3. Horário customizado pelo .env ─────────────────────────────────────
print("\n[3] HORA_LIGAR/HORA_DESLIGAR do .env")

os.environ["HORA_LIGAR"] = "07:00"
os.environ["HORA_DESLIGAR"] = "23:00"
checar("janela que NÃO cruza a meia-noite (07:00-23:00): 12:00 dentro",
       janela.dentro_da_janela(em("12:00")))
checar("janela que NÃO cruza a meia-noite: 00:30 fora",
       not janela.dentro_da_janela(em("00:30")))

os.environ["HORA_LIGAR"] = "banana"
checar("horário inválido cai no padrão em vez de derrubar o processo",
       janela.hora_ligar().strftime("%H:%M") == janela.HORA_LIGAR_PADRAO)

os.environ["HORA_LIGAR"] = "08:30"
os.environ["HORA_DESLIGAR"] = "02:00"

# ── 4. Supervisor: quando ele age e quando não ───────────────────────────
print("\n[4] garantir_bot.py — tabela de decisão")

import garantir_bot  # noqa: E402
import startup  # noqa: E402
from core import pausa  # noqa: E402

# A bandeira de pausa é um arquivo real compartilhado pelos 4 processos do
# bot. Usar o caminho de produção aqui e abortar no meio deixaria o bot do
# Daniel PAUSADO em silêncio — exatamente o estado mais difícil de perceber.
_TMP = tempfile.mkdtemp(prefix="bot_ciclo_")
pausa.FLAG_PATH = os.path.join(_TMP, "pausado.flag")
garantir_bot.LOG_CICLO = os.path.join(_TMP, "shutdown.log")

_rodando = False
_confiavel = True
startup.rastreador_em_execucao = lambda: _rodando  # type: ignore[assignment]
# psutil não é instalado no CI (os testes só precisam de dotenv + requests),
# então a checagem real responderia "não sei" em todos os casos abaixo e
# esconderia justamente a tabela de decisão que se quer testar.
startup.checagem_de_processos_confiavel = lambda: _confiavel  # type: ignore[assignment]

d = garantir_bot.diagnostico(em("12:00"))
checar("dentro da janela, bot fora do ar → sobe", d["precisa_subir"], str(d))

_rodando = True
d = garantir_bot.diagnostico(em("12:00"))
checar("bot já rodando → não sobe um segundo", not d["precisa_subir"], str(d))

_rodando = False
d = garantir_bot.diagnostico(em("05:00"))
checar("fora da janela (madrugada) → não sobe", not d["precisa_subir"], str(d))
checar("e o motivo diz que o PC deveria estar desligado",
       "fora da janela" in d["motivo"], d["motivo"])

pausa.pausar("teste do ciclo diário", origem="teste")
d = garantir_bot.diagnostico(em("12:00"))
checar("pausa ativa → não ressuscita por cima da decisão do operador",
       not d["precisa_subir"], str(d))
pausa.retomar()

d = garantir_bot.diagnostico(em("12:00"))
checar("pausa removida → volta a subir", d["precisa_subir"], str(d))

# Sem psutil, `rastreador_em_execucao()` devolve False para "não está" E para
# "não consegui olhar". Agir sobre esse False sobe um segundo conjunto de
# rastreadores a cada 30 min — dois processos publicando a mesma oferta no
# canal, 48 vezes por dia. Na dúvida, o supervisor não sobe nada.
_confiavel = False
d = garantir_bot.diagnostico(em("12:00"))
checar("checagem cega (sem psutil) → NÃO sobe um possível segundo bot",
       not d["precisa_subir"], str(d))
checar("e o motivo diz por quê, com a correção",
       "psutil" in d["motivo"] and "pip install" in d["motivo"], d["motivo"])
checar("o diagnóstico expõe que a checagem não é confiável",
       d["checagem_confiavel"] is False, str(d))

# Fora da janela a cegueira não importa: não se sobe nada de madrugada de
# qualquer jeito, e o alerta seria ruído às 03h.
d = garantir_bot.diagnostico(em("05:00"))
checar("cego mas fora da janela → segue sendo só 'PC desligado'",
       not d["precisa_subir"] and "fora da janela" in d["motivo"], str(d))
_confiavel = True

# `subir_bot()` chamando Popen não é o mesmo que o bot ter ficado de pé: com
# o .env inválido o startup.py sai em ~1s. Anunciar "reiniciado" nesse caso
# faria o log dizer que o ciclo fechou enquanto os grupos ficavam sem oferta.
_orig_popen = garantir_bot.subprocess.Popen


class _ProcMorto:
    def __init__(self, *a, **k):
        pass

    def wait(self, timeout=None):
        return 1  # saiu na hora, como o startup.py com config inválida


class _ProcVivo:
    def __init__(self, *a, **k):
        pass

    def wait(self, timeout=None):
        raise garantir_bot.subprocess.TimeoutExpired("startup.py", timeout)


garantir_bot.GRACA_SUBIDA_S = 0.1
garantir_bot.subprocess.Popen = _ProcMorto  # type: ignore[assignment]
checar("startup.py que morre na carência → subir_bot() responde FALHA",
       garantir_bot.subir_bot() is False)
garantir_bot.subprocess.Popen = _ProcVivo  # type: ignore[assignment]
checar("startup.py que continua vivo → subir_bot() responde sucesso",
       garantir_bot.subir_bot() is True)
garantir_bot.subprocess.Popen = _orig_popen  # type: ignore[assignment]

# ── 3b. A nuvem não pode publicar por cima do PC ligado ──────────────────
print("\n[3b] pc_pode_estar_publicando() — quem publica de fora espera a carência")

# O GitHub Actions publica na janela em que o PC dorme. Mas às 02:00 a janela
# já diz "fora" enquanto o aguardar_e_desligar.ps1 pode estar esperando até
# 35 min o bot terminar a rodada — e nesse intervalo o PC continua postando.
# Os dois bancos de deduplicação são separados (o da nuvem é um cache do
# Actions), então a sobreposição publica a MESMA oferta duas vezes no canal.
checar("01:59 (dentro da janela) → a nuvem espera",
       janela.pc_pode_estar_publicando(em("01:59")))
checar("02:00 (janela ja diz 'fora', mas o desligamento pode estar esperando)",
       janela.pc_pode_estar_publicando(em("02:00"))
       and not janela.dentro_da_janela(em("02:00")))
checar("02:34 (ultimo minuto da carencia de 35min) → ainda espera",
       janela.pc_pode_estar_publicando(em("02:34")))
checar("02:35 (fim da carencia) → a nuvem publica",
       not janela.pc_pode_estar_publicando(em("02:35")))
checar("08:29 (PC ainda dormindo) → a nuvem publica",
       not janela.pc_pode_estar_publicando(em("08:29")))
checar("08:30 (PC religou) → a nuvem espera",
       janela.pc_pode_estar_publicando(em("08:30")))

# O bot.yml tem de consultar a fonte unica, nao repetir o horario no cron.
_botyml = open(os.path.join(RAIZ, ".github", "workflows", "bot.yml"), encoding="utf-8").read()
# A pergunta continua sendo "o PC pode estar publicando agora?", mas quem
# responde passou a ser `core/papel.py` — que consulta `core/janela.py` por
# dentro. A indirecao existe porque agora sao TRES publicadores (PC, este
# workflow e o servidor em deploy/), e cada um so precisa dizer quem e.
checar("bot.yml pergunta ao core/papel.py antes de publicar",
       "core.papel --pode-publicar" in _botyml)
checar("e o papel do workflow e configuravel pelo repositorio",
       "vars.PAPEL" in _botyml,
       "sem isso, aposentar o Actions exigiria editar codigo")
checar("o papel do workflow, sem variavel definida, e 'nuvem'",
       "'nuvem'" in _botyml,
       "papel vazio vira 'local' (sem trava) — errado para o Actions")
checar("bot.yml roda com o fuso de Sao Paulo",
       "America/Sao_Paulo" in _botyml,
       "sem TZ o runner compara o horario UTC com a janela em BRT")
checar("os passos que publicam respeitam a decisao da janela",
       _botyml.count("steps.janela.outputs.pular != '1'") >= 6)
checar("bot.yml aceita HORA_LIGAR/HORA_DESLIGAR do repositorio",
       "vars.HORA_LIGAR" in _botyml and "vars.HORA_DESLIGAR" in _botyml,
       "senao mudar o horario deixa a nuvem para tras, publicando por cima")

# Medido na execucao 255 (2026-09-03): a Amazon leva ~3 min de um orcamento
# de 10 min do job, e roda ANTES dos passos que salvam a deduplicacao e
# atualizam o site. Se um dia passar do orcamento, o `continue-on-error` nao
# ajuda — o timeout do JOB mata tudo, o banco nao e salvo, e a rodada
# seguinte REPUBLICA as mesmas ofertas.
import re as _re2
_amazon = _botyml[_botyml.index("Rodar cupons Amazon"):]
_amazon = _amazon[:_amazon.index("- name: Salvar banco")]
checar("o passo da Amazon tem teto de tempo proprio",
       _re2.search(r"timeout-minutes:\s*\d+", _amazon) is not None,
       "sem teto proprio, uma Amazon pendurada consome o job inteiro")
checar("e segue com continue-on-error (Amazon nao bloqueia o ML)",
       "continue-on-error: true" in _amazon)

# ── 4b. O comando único que registra o ciclo ─────────────────────────────
print("\n[4b] configurar_ciclo.ps1 — o caminho de instalação do ciclo")

_ciclo = open(os.path.join(RAIZ, "configurar_ciclo.ps1"), encoding="utf-8").read()
checar("chama o agendador em vez de repetir a lógica dele",
       "agendar_shutdown.ps1" in _ciclo)
checar("zera $LASTEXITCODE antes de ler o resultado do agendador",
       '$global:LASTEXITCODE = 0' in _ciclo,
       "sem isso ele relata o código de saída do git que rodou antes")
checar("captura o erro terminante do agendador",
       "catch {" in _ciclo and "$ok = $false" in _ciclo)
checar("mostra o -Status depois — a prova de que as tarefas existem",
       "-Status" in _ciclo)
checar("tem código de saída explícito nos dois caminhos",
       "exit 0" in _ciclo and "exit 1" in _ciclo)
checar("recusa rodar fora do Windows em uma linha, sem parede de erro",
       "$IsWindows" in _ciclo)

# O instalador e o "comando de atualizacao do sistema": terminar sem olhar o
# WhatsApp era o que fazia o grupo ficar mudo sem ninguem perceber.
_inst = open(os.path.join(RAIZ, "instalar_tudo.ps1"), encoding="utf-8").read()
checar("instalar_tudo.ps1 termina conferindo o caminho do WhatsApp",
       "diagnostico_whatsapp.py" in _inst)
checar("e registra pendencia quando o WhatsApp esta bloqueado",
       "WhatsApp bloqueado" in _inst)

_bat = open(os.path.join(RAIZ, "CICLO_DIARIO.bat"), encoding="utf-8").read()
checar("o .bat chama o .ps1 com ExecutionPolicy Bypass",
       "configurar_ciclo.ps1" in _bat and "ExecutionPolicy Bypass" in _bat,
       "a política padrão do Windows recusa .ps1 e o erro não explica isso")

# Horário escrito à mão em segundo arquivo foi o que fez o watchdog alertar
# "bot caiu" toda madrugada num desligamento planejado (Regra 15).
for _arq in ("CHECKLIST_SISTEMA.md", "agendar_shutdown.ps1"):
    _txt = open(os.path.join(RAIZ, _arq), encoding="utf-8").read()
    checar(f"{_arq} não menciona mais o horário antigo de religar (08:45)",
           "08:45" not in _txt, _arq)

# ── 5. O watchdog do n8n, executado de verdade ───────────────────────────
print("\n[5] n8n W1 'Checar heartbeat' — JS real, relógio simulado")

WF = os.path.join(RAIZ, "n8n", "workflows", "01-ingestao-e-watchdog.json")

if not shutil.which("node"):
    print("  ⚠️  Node não encontrado — parte JS PULADA (não é falha).")
else:
    RUNNER = os.path.join(_TMP, "runner_watchdog.js")
    with open(RUNNER, "w", encoding="utf-8") as f:
        f.write(
            # Executa o Code node com Date.now() fixo. É o único jeito de
            # provar o comportamento às 02:30 sem esperar as 02:30.
            "const fs = require('fs');\n"
            "const [, , wfFile, nodeName, agoraMs, storeFile] = process.argv;\n"
            "const wf = JSON.parse(fs.readFileSync(wfFile, 'utf8'));\n"
            "const node = wf.nodes.find(n => n.name === nodeName);\n"
            "let store = {};\n"
            "try { store = JSON.parse(fs.readFileSync(storeFile, 'utf8')); } catch (e) {}\n"
            "const fixo = Number(agoraMs);\n"
            "Date.now = () => fixo;\n"
            "const fn = new Function('$json', '$getWorkflowStaticData',\n"
            "                        node.parameters.jsCode);\n"
            "const out = fn({}, () => store);\n"
            "fs.writeFileSync(storeFile, JSON.stringify(store));\n"
            "console.log(JSON.stringify(out));\n"
        )

    # O instalador preenche admin_chat_id e sincroniza a janela; testar o
    # JSON cru mediria um estado em que o workflow nunca roda.
    sys.path.insert(0, os.path.join(RAIZ, "n8n"))
    os.environ.setdefault("ADMIN_CHAT_ID", "555000111")
    from setup_n8n import _valores_config, preparar_workflow  # noqa: E402

    with open(WF, encoding="utf-8") as f:
        _wf = preparar_workflow(json.load(f), {}, _valores_config())
    WF_PRONTO = os.path.join(_TMP, "w1.json")
    with open(WF_PRONTO, "w", encoding="utf-8") as f:
        json.dump(_wf, f, ensure_ascii=False)

    def rodar(quando: datetime, store: dict) -> tuple[dict, dict]:
        store_file = os.path.join(_TMP, "store.json")
        with open(store_file, "w", encoding="utf-8") as f:
            json.dump(store, f)
        # O nó lê o relógio em UTC e converte com fuso_horas = -3; o teste
        # pensa em horário de Brasília, então soma as 3h de volta.
        ms = int((quando + timedelta(hours=3)).timestamp() * 1000)
        r = subprocess.run(
            ["node", RUNNER, WF_PRONTO, "Checar heartbeat", str(ms), store_file],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            raise AssertionError(f"runner falhou: {r.stderr[:300]}")
        with open(store_file, encoding="utf-8") as f:
            return json.loads(r.stdout)[0]["json"], json.load(f)

    checar("a janela do workflow foi sincronizada com core/janela.py",
           "silencio_de: '02:00'" in json.dumps(_wf) and "silencio_ate: '08:30'" in json.dumps(_wf))

    # A sequência real de uma noite atravessa a meia-noite: o último
    # heartbeat é de ONTEM às 23h e as checagens da madrugada são de HOJE.
    # Com tudo no mesmo dia, a idade do heartbeat sai negativa e o teste
    # mede uma situação que nunca acontece.
    ONTEM, HOJE = "2026-08-28", "2026-08-29"

    # 23:00 de ontem — bot vivo, heartbeat de 2 min atrás. Nada a alertar.
    st = {"ultimo_heartbeat_ts":
          int((em("23:00", ONTEM) + timedelta(hours=3)).timestamp() * 1000) - 120_000}
    out, st = rodar(em("23:00", ONTEM), st)
    checar("23:00 com heartbeat fresco → sem alerta", not out.get("alertar"), str(out))

    # 02:30 — PC desligado por agendamento há 30 min. É o caso que gerava um
    # falso alarme por dia; agora tem que ficar calado E marcar a queda como
    # planejada.
    out, st = rodar(em("02:30", HOJE), st)
    checar("02:30 (silêncio) → NÃO alerta", not out.get("alertar"), str(out))
    checar("02:30 → motivo é a janela de silêncio",
           "silencio" in (out.get("motivo") or ""), str(out))
    checar("02:30 → marca a queda como planejada", st.get("queda_planejada") is True)

    # 07:00 — ainda dentro do silêncio, várias execuções depois. Continua calado.
    out, st = rodar(em("07:00", HOJE), st)
    checar("07:00 (ainda no silêncio) → segue sem alertar", not out.get("alertar"), str(out))

    # 08:45 — 15 min após religar, ainda dentro da tolerância de 45 min.
    # Um bot que demora a subir não pode virar alarme.
    out, st_tol = rodar(em("08:45", HOJE), dict(st))
    checar("08:45 (dentro da tolerância de 45min) → ainda não alerta",
           not st_tol.get("alerta_queda_enviado"), str(out))

    # 09:20 — 50 min após religar e o heartbeat continua velho: o PC não
    # voltou. ESTE é o alerta que salva o dia de publicação.
    out, st2 = rodar(em("09:20", HOJE), dict(st))
    checar("09:20 sem heartbeat → alerta 'o PC nao religou'",
           out.get("alertar") and "nao religou" in (out.get("texto") or ""), str(out)[:300])

    # Regressão do bug encontrado por este teste: na primeira versão, a
    # checagem das 08:45 CONSUMIA a marca `queda_planejada`. Duas coisas
    # quebravam juntas — às 08:45 o nó caía no alerta genérico ("parou de
    # responder há 587 min", contando as horas de sono planejado) e às 09:20
    # já não sabia que a queda tinha sido planejada. Rodar a sequência no
    # MESMO store é o que expõe isso; com stores independentes os dois casos
    # passavam separados.
    st_seq = dict(st)
    _, st_seq = rodar(em("08:45", HOJE), st_seq)
    checar("08:45 preserva a marca de queda planejada para a checagem seguinte",
           st_seq.get("queda_planejada") is True, str(st_seq))
    out_seq, st_seq = rodar(em("09:20", HOJE), st_seq)
    checar("e 09:20 na sequência ainda dá o alerta certo ('nao religou')",
           "nao religou" in (out_seq.get("texto") or ""), str(out_seq)[:200])

    # Mesmo horário, mas o PC voltou e mandou heartbeat: nenhum alerta.
    st3 = dict(st)
    st3["ultimo_heartbeat_ts"] = int((em("09:15", HOJE) + timedelta(hours=3)).timestamp() * 1000)
    out, _ = rodar(em("09:20", HOJE), st3)
    checar("09:20 com o PC de volta → nenhum alerta",
           not out.get("alertar"), str(out)[:200])

    # Queda de verdade no meio da tarde: o watchdog continua fazendo o
    # trabalho dele. A janela de silêncio não pode ter virado uma mordaça.
    st4 = {"ultimo_heartbeat_ts": int((em("14:00", HOJE) + timedelta(hours=3)).timestamp() * 1000)}
    out, _ = rodar(em("15:00", HOJE), st4)
    checar("15:00 com 60min sem sinal → alerta normal de queda",
           out.get("alertar") and "parou de responder" in (out.get("texto") or ""), str(out)[:300])

shutil.rmtree(_TMP, ignore_errors=True)

# ── aplicar_tudo.ps1 ─────────────────────────────────────────────────────
# O roteiro manual (stop -> fetch -> checkout -> configurar -> start) tem
# cinco pontos de falha silenciosa, e o script existe para fechar os cinco.
# Cada asserção abaixo guarda um deles.
print("\n[6] aplicar_tudo.ps1 — as travas dos cinco pontos de falha")
_apl = os.path.join(RAIZ, "aplicar_tudo.ps1")
checar("aplicar_tudo.ps1 existe", os.path.isfile(_apl))
if os.path.isfile(_apl):
    _t = open(_apl, encoding="utf-8").read()
    # 1. checkout sobre arvore suja: o git recusa e os passos seguintes
    #    rodariam no codigo ANTIGO achando que estao no novo.
    checar("guarda alteracao local antes do checkout",
           "git stash push" in _t and "git status --porcelain" in _t)
    # 2. registrar tarefa do Agendador exige elevacao; sem checar, o ciclo
    #    fica pela metade sem dizer qual metade.
    checar("exige Administrador para mexer no Agendador",
           "IsInRole" in _t and "-SemAgenda" in _t)
    # 3. a licao do aguardar_e_desligar.ps1: Get-Command python sem
    #    -ErrorAction derruba o script inteiro, sem log.
    # Vale para TODO Get-Command do arquivo, nao so o do python: com
    # $ErrorActionPreference = "Stop" um deles sem -ErrorAction derruba o
    # script inteiro, sem log — a armadilha do aguardar_e_desligar.ps1.
    _gc = [l.strip() for l in _t.splitlines()
           if "Get-Command" in l and not l.strip().startswith("#")]
    checar("nenhum Get-Command pode derrubar o script",
           _gc and all("-ErrorAction SilentlyContinue" in l for l in _gc),
           f"{len(_gc)} chamada(s); sem guarda: "
           + str([l for l in _gc if "-ErrorAction SilentlyContinue" not in l]))
    # 4. Regra 15: "chamar Popen nao e o mesmo que ter subido".
    checar("confirma que o startup.py sobreviveu a carencia",
           "startup.py*" in _t and "Start-Sleep" in _t)
    # 5. Regra 10: nunca parar no meio de uma rodada.
    checar("consulta execucao_em_andamento antes de parar",
           "execucao_em_andamento" in _t)
    # 6. "Existe no PATH" nao e "funciona". Dois casos reais, vistos juntos
    #    no PC do Daniel em 08/09/2026 numa janela de Administrador:
    #    o `python` do PATH era o ATALHO da Microsoft Store (executado, so
    #    abre a loja), e o `git` nao existia — elevar trocou o perfil de
    #    usuario e, com ele, o PATH.
    checar("testa git e python de verdade, nao so se existem",
           "Testar-Programa" in _t and "--version" in _t)
    checar("ignora o atalho da Microsoft Store",
           "WindowsApps" in _t)
    checar("procura fora do PATH quando ele nao serve",
           "Achar-Programa" in _t and "LOCALAPPDATA" in _t)
    # 7. Comando inexistente lanca CommandNotFoundException e NAO mexe em
    #    $LASTEXITCODE: a guarda `if ($LASTEXITCODE -ne 0)` nao pegava nada e
    #    o script seguia com todos os passos de git falhando em silencio.
    for _cmd in ("status --porcelain", "fetch origin", "checkout"):
        checar(f"git '{_cmd}' chamado pelo caminho encontrado",
               f"& $git {_cmd}" in _t,
               "chamada solta a 'git' volta a depender do PATH")

    # 8. O PATH pode simplesmente nao ter git/python (visto em 08/09/2026).
    #    O registro do Windows guarda onde os instaladores puseram cada um —
    #    independe do PATH e de quem elevou a janela.
    checar("procura tambem no registro do Windows",
           "Caminhos-Do-Registro" in _t and "GitForWindows" in _t
           and "PythonCore" in _t)
    checar("aceita o caminho na mao quando a busca falha",
           "[string]$Git" in _t and "[string]$Python" in _t)
    checar("diz ONDE procurou quando nao acha",
           "Onde-Procurei" in _t)
    # Os filhos (start.ps1 e cia) fazem a propria busca e cairiam no mesmo
    # atalho da Loja. Herdar o PATH do processo resolve — e e do PROCESSO,
    # nada gravado na maquina (Regra 10).
    checar("passa a escolha aos scripts filhos pelo PATH do processo",
           "$env:PATH = (Split-Path $python)" in _t)
    checar("nao grava PATH na maquina",
           "SetEnvironmentVariable" not in _t and "[Environment]::SetEnv" not in _t)

    # 9. git AUSENTE NAO PODE IMPEDIR A PUBLICACAO. O git so traz codigo
    #    novo; quem publica no WhatsApp e o whatsapp_queue_sender.py, filho
    #    do startup.py, e nenhum dos dois toca em git. Tratar git como
    #    obrigatorio deixava o bot parado por causa de uma ferramenta que a
    #    publicacao nao usa (erro de desenho meu, corrigido em 08/09/2026).
    _bloco_git = _t.split("if (-not $git)", 1)
    checar("sem git o script avisa e segue, nao aborta",
           len(_bloco_git) > 1 and "exit 1" not in _bloco_git[1].split("else")[0],
           "sem git ainda aborta")
    checar("sem git o passo de trazer a branch e pulado",
           "PULADO (sem git)" in _t)
    # E o python continua sendo fatal — sem ele nao ha bot nenhum.
    checar("sem python continua abortando",
           "nao achei um 'python' que funcione" in _t
           and "exit 1" in _t.split("nao achei um 'python'")[1][:600])

    # 10. Python recem-instalado nao tem NADA (nem python-telegram-bot, nem
    #     playwright). Sem instalar, o passo de import real falha com
    #     ModuleNotFoundError e parece erro de codigo.
    checar("instala as dependencias quando faltam",
           "pip install" in _t and "requirements.txt" in _t)
    checar("instala tambem o navegador do Playwright",
           "playwright install chromium" in _t,
           "sem o navegador a raspagem do ML nao roda")

    # Regra 10: o processo que sobe e o PAI.
    checar("sobe pelo start.ps1 (processo pai), nao pelos filhos",
           "start.ps1" in _t and "rastreador.py" not in _t.split("Passo 9")[-1])

# ── coletar_diagnostico.ps1 ──────────────────────────────────────────────
print("\n[7] coletar_diagnostico.ps1 — nada de segredo sai no zip")
_col = os.path.join(RAIZ, "coletar_diagnostico.ps1")
checar("coletar_diagnostico.ps1 existe", os.path.isfile(_col))
if os.path.isfile(_col):
    _c = open(_col, encoding="utf-8").read()
    _regras = re.findall(r"-replace '([^']+)', '\*\*\*REDIGIDO\*\*\*'", _c)
    checar("tem regras de redacao", len(_regras) >= 3, f"achei {len(_regras)}")
    # A prova que importa: as regras do .ps1, aplicadas ao log que REALMENTE
    # vazou neste repositorio, nao podem deixar token nenhum passar. Sem
    # isto, o zip so mudaria o vazamento de lugar.
    _amostra = subprocess.run(["git", "show", "2c57563:rastreador.log"],
                              cwd=RAIZ, capture_output=True).stdout.decode("utf-8", "replace")
    _pad = r"\d{8,12}:AA[\w-]{30,}"
    if _amostra and re.search(_pad, _amostra):
        _saida = _amostra
        for _r in _regras:
            _saida = re.sub(_r, "***REDIGIDO***", _saida)
        checar("redige por completo o log que vazou de verdade",
               not re.search(_pad, _saida))
    checar("nao copia o .env", "_env_CHAVES" in _c and "(preenchido," in _c)
    checar("pula perfil de navegador e arquivo de token",
           "token|cookie|session" in _c)
    checar("le o corpo do /health mesmo em 503",
           "GetResponseStream" in _c)

# ── Codificacao dos .ps1 ─────────────────────────────────────────────────
# O Windows PowerShell 5.1 (o que vem no Windows) le um .ps1 SEM BOM como
# cp1252, nao como UTF-8. Um travessao "—" (E2 80 94) vira "â€”", e o "\u201d"
# do cp1252 (0x94) e uma ASPA que o parser aceita como fim de string: a
# string fecha no meio e o arquivo inteiro deixa de ser analisavel.
#
# Nao e teorico. Em 07/09/2026, 10 dos 15 .ps1 estavam assim — start.ps1 e
# stop.ps1 entre eles (o Daniel nao conseguia iniciar nem parar o bot), e
# tambem aguardar_e_desligar.ps1 e acordar_e_iniciar.ps1, ou seja, o ciclo
# liga/desliga nao tinha como funcionar. O erro no PC dele foi:
#   ')' de fechamento ausente na expressao.
print("\n[8] .ps1 legiveis pelo Windows PowerShell (BOM UTF-8)")
_ps1 = subprocess.run(["git", "ls-files", "*.ps1"], cwd=RAIZ,
                      capture_output=True, text=True).stdout.split()
checar("achei os .ps1 do repositorio", len(_ps1) >= 10, f"{len(_ps1)} arquivos")
# Caracteres que, decodificados como cp1252, viram aspa ou apostrofo — sao
# estes que quebram o PARSER, nao a acentuacao comum (que so sai feia).
_PERIGOSOS = "–—‘’“”"
for _f in _ps1:
    _b = open(os.path.join(RAIZ, _f), "rb").read()
    _tem_bom = _b.startswith(b"\xef\xbb\xbf")
    _txt = _b.decode("utf-8-sig")
    _n = sum(_txt.count(c) for c in _PERIGOSOS)
    checar(f"{_f} tem BOM UTF-8", _tem_bom,
           f"sem BOM e com {_n} caractere(s) que quebram o parser" if _n
           else "sem BOM (acentos sairiam errados)")

# ── Resultado ────────────────────────────────────────────────────────────
print()
if _falhas:
    print(f"❌ {len(_falhas)} falha(s) de {_ok + len(_falhas)}:")
    for f in _falhas:
        print(f"   · {f}")
    sys.exit(1)
print(f"✅ CICLO DIÁRIO VALIDADO — {_ok}/{_ok} verificações passaram.")
