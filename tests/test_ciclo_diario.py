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

# ── Resultado ────────────────────────────────────────────────────────────
print()
if _falhas:
    print(f"❌ {len(_falhas)} falha(s) de {_ok + len(_falhas)}:")
    for f in _falhas:
        print(f"   · {f}")
    sys.exit(1)
print(f"✅ CICLO DIÁRIO VALIDADO — {_ok}/{_ok} verificações passaram.")
