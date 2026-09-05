# -*- coding: utf-8 -*-
"""
SUPERVISOR DA JANELA — garante que, com o PC ligado dentro do horário de
operação, o bot esteja de pé. Roda de 30 em 30 minutos pela tarefa
`BotOfertas-Supervisor`.

Por que existe
--------------
O ciclo diário depende de UMA tarefa acertar UM instante: às 08:30 o
`BotOfertas-WakeUp` acorda o PC e sobe o bot. Se esse instante falha — queda
de energia durante a madrugada, alguém desligou a máquina no botão, o
Windows subiu uma atualização e engoliu o gatilho, o `startup.py` morreu às
11h — não existe segunda chance: o bot fica fora do ar o dia inteiro e não
publica nada nos grupos. Foi assim em 31/07/2026, quando o ciclo falhou em
silêncio e o único sintoma foi o PC ligado de manhã sem nada rodando.

Uma tarefa que repete a cada 30 min transforma "acertar um instante" em
"acertar qualquer instante do dia": no pior caso o bot volta meia hora
depois, sozinho. É a diferença entre perder um dia de publicação e perder
uma rodada.

O que ele NÃO faz
-----------------
- Não sobe nada fora da janela de operação (02:00–08:30): nesse intervalo o
  PC está desligado por decisão, e se alguém o ligou de madrugada não é o
  supervisor que vai começar a publicar.
- Não sobe nada com a pausa ativa (`core.pausa`): pausa é uma decisão
  explícita e ressuscitar o bot por cima dela seria ignorar o operador.
- Não sobe um segundo bot: pergunta ao `startup.py` — a mesma checagem que
  ele faz — antes de qualquer coisa.
- Não age quando não consegue enxergar. Sem psutil a pergunta "está
  rodando?" responde False para tudo, inclusive com o bot de pé; agir sobre
  esse False duplicaria os rastreadores a cada 30 min. Nesse caso ele
  registra, alerta e sai com erro — sem subir nada.
- Não reinicia só os filhos. Sobe o processo PAI (`python -u startup.py`),
  como manda a Regra 10 do CLAUDE.md.

Uso:
    python garantir_bot.py              # decide e age
    python garantir_bot.py --somente-checar   # só diagnostica, não sobe nada
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

LOG_CICLO = os.path.join(BASE, "data", "shutdown.log")

# Quanto esperar para concluir que o startup.py ficou de pe. Ele leva
# menos de 1s para desistir quando a configuracao esta invalida.
GRACA_SUBIDA_S = 8

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE, ".env"))
except Exception:
    pass

from core import janela, pausa  # noqa: E402

log = logging.getLogger("supervisor")


def _registrar(linha: str) -> None:
    """Grava no mesmo log do ciclo diário (`data/shutdown.log`).

    Suspensão, despertar e agora as intervenções do supervisor ficam no
    mesmo arquivo, em ordem: é lendo essas linhas em sequência que se
    descobre se o ciclo fechou, e um arquivo separado só para o supervisor
    obrigaria a cruzar dois logs à mão para responder a mesma pergunta.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(LOG_CICLO), exist_ok=True)
    try:
        with open(LOG_CICLO, "a", encoding="utf-8") as f:
            f.write(f"{ts} - {linha}\n")
    except OSError as e:
        # Log cheio/disco sem espaço não pode impedir o bot de subir: o
        # objetivo do supervisor é a subida, o registro é secundário.
        print(f"[supervisor] nao consegui escrever no log: {e}", file=sys.stderr)
    print(f"[supervisor] {linha}")


def diagnostico(agora: datetime | None = None) -> dict:
    """Estado que decide a ação — separado da ação para poder ser testado
    sem subir processo nenhum."""
    agora = agora or datetime.now()
    from startup import checagem_de_processos_confiavel, rastreador_em_execucao

    dentro = janela.dentro_da_janela(agora)
    rodando = rastreador_em_execucao()
    pausado = pausa.pausado()
    confiavel = checagem_de_processos_confiavel()

    if not dentro:
        motivo = "fora da janela de operacao (PC deveria estar desligado)"
    elif pausado:
        motivo = "pausa ativa — nao vou ressuscitar por cima de uma decisao do operador"
    elif not confiavel:
        # Sem psutil, "esta rodando?" responde False sempre. Agir sobre esse
        # False sobe um segundo conjunto de rastreadores a cada 30 min — e
        # dois rastreadores publicando em paralelo é post repetido no canal,
        # 48 vezes por dia. Na duvida, nao subir: o custo de esperar e uma
        # rodada; o de duplicar e o grupo inteiro vendo a mesma oferta.
        motivo = (
            "psutil indisponivel — nao da para saber se o bot ja esta de pe; "
            "nao vou arriscar uma segunda instancia publicando em paralelo "
            "(corrija com: pip install -r requirements.txt)"
        )
    elif rodando:
        motivo = "bot ja em execucao"
    else:
        motivo = "bot fora do ar dentro da janela"

    return {
        "agora": agora.isoformat(timespec="seconds"),
        "dentro_da_janela": dentro,
        "rodando": rodando,
        "pausado": pausado,
        "checagem_confiavel": confiavel,
        "precisa_subir": dentro and not rodando and not pausado and confiavel,
        "motivo": motivo,
    }


def subir_bot() -> bool:
    """Sobe o processo PAI, desacoplado deste (que morre em seguida).

    `Popen` sem `wait()` e sem herdar o console: a tarefa agendada termina
    em segundos e o bot continua vivo. Se o supervisor segurasse o processo,
    a tarefa ficaria "rodando" para sempre e a próxima repetição de 30 min
    seria descartada por `IgnoreNew` — o supervisor mataria a si mesmo.
    """
    python = sys.executable or "python"
    kwargs: dict = {"cwd": BASE, "close_fds": True}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: o bot sobrevive ao
        # fim da tarefa agendada e não recebe o Ctrl+C dela.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen([python, "-u", os.path.join(BASE, "startup.py")], **kwargs)
    except OSError as e:
        _registrar(f"Supervisor FALHOU ao subir o bot: {e}")
        return False

    # Chamar Popen nao e o mesmo que o bot ter subido: com o .env invalido o
    # startup.py sai em ~1s (etapa_1 chama sys.exit(1)). Sem esperar por
    # isso, o supervisor anunciaria "bot reiniciado" a cada 30 min para
    # sempre, e o log diria que o ciclo fechou enquanto os grupos ficavam
    # sem oferta o dia inteiro. Esperar poucos segundos nao atrapalha: a
    # tarefa so roda de meia em meia hora, e o caminho normal (startup.py
    # entra em monitorar()) nunca retorna dentro da carencia.
    try:
        codigo = proc.wait(timeout=GRACA_SUBIDA_S)
    except subprocess.TimeoutExpired:
        return True
    _registrar(
        f"Supervisor subiu o startup.py, mas ele saiu em menos de "
        f"{GRACA_SUBIDA_S}s (codigo {codigo}) — ver data/bot.log"
    )
    return False


def _avisar_n8n(evento: str, dados: dict) -> None:
    """Melhor esforço: o n8n não pode atrasar nem impedir a subida do bot
    (Regra 13). Qualquer falha aqui é engolida de propósito."""
    try:
        from integrations import n8n

        n8n.emitir(evento, dados)
    except Exception:
        pass


def main() -> int:
    d = diagnostico()

    if "--somente-checar" in sys.argv:
        import json

        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0 if not d["precisa_subir"] else 1

    if not d["precisa_subir"]:
        if d["dentro_da_janela"] and not d["checagem_confiavel"]:
            # Este nao e um "nada a fazer": e o supervisor cego, sem poder
            # garantir nada dentro do horario de operacao. Merece linha no
            # log, alerta e falha visivel no Agendador de Tarefas.
            _registrar(f"Supervisor SEM VISIBILIDADE: {d['motivo']}")
            _avisar_n8n("supervisor_cego", {"motivo": d["motivo"]})
            return 1
        # Silêncio no log: rodando a cada 30 min, registrar "tudo certo"
        # encheria o shutdown.log com 48 linhas por dia e enterraria as
        # que importam (suspensão, despertar, intervenção).
        print(f"[supervisor] nada a fazer — {d['motivo']}")
        return 0

    _registrar(f"Supervisor: {d['motivo']} — subindo startup.py")
    if not subir_bot():
        _avisar_n8n("supervisor_falhou", {"motivo": d["motivo"]})
        return 1

    _avisar_n8n(
        "bot_reiniciado",
        {
            "origem": "supervisor",
            "motivo": d["motivo"],
            "dentro_da_janela": d["dentro_da_janela"],
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
