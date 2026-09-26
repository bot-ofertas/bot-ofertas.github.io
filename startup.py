# -*- coding: utf-8 -*-
"""
STARTUP — Inicialização sequencial e resiliente do bot de ofertas.

Ordem correta:

    1. Validar configurações (.env, TOKEN_TELEGRAM)
    2. Verificar WhatsApp Desktop instalado (janela detectável)
    3. Iniciar healthcheck HTTP (:8724/health)
    4. Iniciar rastreador em loop (Telegram sempre, WhatsApp best-effort)

Regra de ouro: Telegram NUNCA depende do WhatsApp. Se o WhatsApp Desktop
não estiver aberto, o rastreador continua postando no Telegram sem falha.

WhatsApp usa exclusivamente o app nativo do Windows (WhatsApp Desktop).
O Chrome dedicado do bot foi desativado por padrão — para reativá-lo
como fallback opcional, defina WHATSAPP_CHROME_FALLBACK=1 no .env.

Registrado como tarefa do Windows (BotOfertas-AutoStart) — roda no login.
"""
import logging
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

LOG_PATH = os.path.join(BASE, "data", "rastreador_local.log")
PID_PATH = os.path.join(BASE, "data", "rastreador.pid")

os.makedirs(os.path.join(BASE, "data"), exist_ok=True)

# Logging estruturado (texto rotativo + JSONL para erros — n8n consome)
from core.error_logger import setup_logging  # noqa: E402
setup_logging()
log = logging.getLogger("startup")


def _rastreador_ja_rodando() -> bool:
    """True se QUALQUER UM dos 3 processos (ML, Amazon, Ferramentas) já
    estiver rodando em modo loop.

    Antes só verificava rastreador.py — se startup.py fosse chamado 2x num
    momento em que só o ML estivesse fora do ar (crash/restart), o guard
    dizia "nada rodando" e main() subia um Amazon/Ferramentas duplicados ao
    lado dos originais ainda vivos (double-posting)."""
    try:
        import psutil  # noqa: PLC0415
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                cl = " ".join(p.info.get("cmdline") or [])
                nome = (p.info.get("name") or "").lower()
                if "python" not in nome:
                    continue
                if "rastreador_amazon.py" in cl:
                    return True
                if "rastreador.py" in cl and "--loop" in cl:
                    return True
                if "campanha_ferramentas.py" in cl and "--loop" in cl:
                    return True
                if "rastreador_magalu.py" in cl and "--loop" in cl:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass
    return False


def rastreador_em_execucao() -> bool:
    """Mesma checagem, exposta para fora do módulo.

    O supervisor (`garantir_bot.py`) precisa exatamente desta resposta antes
    de decidir subir o bot. Duplicar a varredura de processos lá seria criar
    duas definições de "está rodando" que envelhecem em ritmos diferentes —
    e é justamente a discordância entre elas que produziria um segundo
    conjunto de rastreadores publicando em paralelo.
    """
    return _rastreador_ja_rodando()


def checagem_de_processos_confiavel() -> bool:
    """True quando `rastreador_em_execucao()` responde com evidência.

    Sem psutil a varredura não acontece e a resposta é sempre False — que é
    indistinguível de "o bot está fora do ar". Aqui isso não faz mal (o
    startup.py erra para o lado de subir, e ele só roda quando alguém pede),
    mas o supervisor roda sozinho a cada 30 min: acreditando nesse False ele
    subiria um conjunto novo de rastreadores por cima do que já está
    publicando, meia em meia hora, o dia inteiro. Quem decide sozinho
    precisa saber a diferença entre "não está" e "não sei".
    """
    try:
        import psutil  # noqa: F401,PLC0415

        return True
    except ImportError:
        return False


def etapa_1_validar_config() -> bool:
    """Valida .env + TOKEN_TELEGRAM."""
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv(os.path.join(BASE, ".env"))
    except Exception as e:
        log.error("[1/4] Falha ao carregar .env: %s", e)
        return False

    token = os.getenv("TOKEN_TELEGRAM")
    if not token:
        log.error("[1/4] TOKEN_TELEGRAM ausente no .env — Telegram não vai funcionar.")
        return False
    log.info("[1/4] Config OK — TOKEN_TELEGRAM presente.")
    return True


def etapa_2_verificar_whatsapp_desktop() -> bool:
    """Verifica se o app WhatsApp Desktop está aberto (janela ou processo)."""
    try:
        from integrations.whatsapp_desktop import _janela_whatsapp  # noqa: PLC0415
    except Exception as e:
        log.warning("[2/4] whatsapp_desktop indisponível: %s", e)
        return False

    w = _janela_whatsapp()
    if w:
        log.info("[2/4] WhatsApp Desktop detectado — envio nativo ativo.")
        return True

    # Tenta detectar via processo (janela pode estar minimizada em tray)
    try:
        import psutil  # noqa: PLC0415
        for p in psutil.process_iter(["name"]):
            n = (p.info.get("name") or "").lower()
            if "whatsapp" in n:
                log.info("[2/4] WhatsApp Desktop rodando (%s) — envio nativo ativo.", n)
                return True
    except ImportError:
        pass

    log.warning("[2/4] WhatsApp Desktop NÃO detectado — só Telegram vai postar. "
                "Abra o WhatsApp Desktop para ativar WhatsApp.")
    return False


def etapa_3_healthcheck() -> None:
    try:
        from core.healthcheck import iniciar_healthcheck  # noqa: PLC0415
        iniciar_healthcheck()
        log.info("[3/4] Healthcheck em http://127.0.0.1:8724/health")
    except Exception as e:
        log.warning("[3/4] Healthcheck não subiu: %s (não crítico).", e)
    # Watchdog do WhatsApp Desktop — reabre app se cair
    try:
        from core.wa_desktop_watchdog import iniciar_wa_watchdog  # noqa: PLC0415
        iniciar_wa_watchdog()
        log.info("[3/4] Watchdog WhatsApp Desktop ativo (checa a cada 60s).")
    except Exception as e:
        log.warning("[3/4] Watchdog WhatsApp Desktop não subiu: %s", e)


# Windows: cada filho e um console app (python.exe). O startup.py sobe
# DESACOPLADO do console — `garantir_bot.py` usa DETACHED_PROCESS para que o
# bot sobreviva ao fim da tarefa agendada — entao nao ha console para os
# filhos herdarem, e o Windows cria UMA JANELA PRETA NOVA para cada um.
#
# Bug real (20/09/2026): o Daniel mandou print de tres janelas pretas
# empilhadas em cima da tela dele, todas com o titulo do python.exe. Elas
# aparecem a cada subida do bot — inclusive a cada vez que o supervisor
# religa, de 30 em 30 min.
#
# As janelas nao mostram NADA: stdout e stderr de cada filho ja vao para o
# arquivo de log logo abaixo. Eram quatro janelas vazias atrapalhando o uso
# do PC, sem nenhuma informacao dentro.
#
# CREATE_NO_WINDOW roda o console app sem criar janela. O repositorio ja
# fazia isso em web/app.py e core/chrome_manager.py; estes quatro Popen e
# que ficaram para tras.
#
# NAO troque isto por pythonw.exe, que e a "solucao" obvia para o mesmo
# problema: sem console, `sys.stdout` vira None, e `rastreador.py` faz
# `sys.stdout.reconfigure(encoding="utf-8")` na linha 16 — AttributeError no
# import, antes de qualquer log. Os tres rastreadores morreriam no ato e o
# bot inteiro sairia do ar, o que e muito pior do que uma janela aberta.
# Verificado em 20/09/2026: so `sys.stdout.write`/`.reconfigure` quebram sem
# console; `print()` e o `logging` sobrevivem (o print do CPython e no-op
# quando sys.stdout e None).
_SEM_JANELA: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
)


def _iniciar_ml():
    """Sobe só o rastreador ML — usado no start inicial e em reinícios isolados
    (um crash do ML não pode gerar um processo Amazon extra desnecessário)."""
    log.info("[4/4] Iniciando rastreador ML (intervalo aleatório ~20 min)…")
    cmd_ml = [
        sys.executable, os.path.join(BASE, "rastreador.py"),
        "--random", "--loop-min", "18", "--loop-max", "22",
    ]
    log_ml = open(LOG_PATH, "a", encoding="utf-8")
    proc_ml = subprocess.Popen(cmd_ml, stdout=log_ml, stderr=log_ml, cwd=BASE, **_SEM_JANELA)
    with open(PID_PATH, "w") as f:
        f.write(str(proc_ml.pid))
    log.info("[4/4] Rastreador ML PID=%d", proc_ml.pid)
    return proc_ml


def _iniciar_amazon():
    """Sobe só o rastreador Amazon — mesma lógica de isolamento do _iniciar_ml."""
    log.info("[4/4] Iniciando rastreador Amazon (intervalo aleatório ~20 min)…")
    amazon_log_path = os.path.join(BASE, "data", "rastreador_amazon.log")
    cmd_az = [
        sys.executable, os.path.join(BASE, "rastreador_amazon.py"),
        "--random", "--loop-min", "18", "--loop-max", "22",
    ]
    log_az = open(amazon_log_path, "a", encoding="utf-8")
    proc_az = subprocess.Popen(cmd_az, stdout=log_az, stderr=log_az, cwd=BASE, **_SEM_JANELA)
    with open(os.path.join(BASE, "data", "rastreador_amazon.pid"), "w") as f:
        f.write(str(proc_az.pid))
    log.info("[4/4] Rastreador Amazon PID=%d", proc_az.pid)
    return proc_az


def _iniciar_magalu():
    """Sobe o rastreador Magazine Luiza — o terceiro marketplace.

    Devolve None quando MAGALU_VITRINE nao esta configurada, e o supervisor
    trata None como "nao existe" em vez de "caiu". Sem esse guard o
    processo subiria, imprimiria o aviso de vitrine ausente, sairia em ~1s
    e queimaria as 3 tentativas do supervisor a cada rodada — o mesmo
    padrao de ruido que a Regra 15 descreve para o `.env` invalido.
    """
    try:
        from affiliates.magalu import magalu_ativo  # noqa: PLC0415
        if not magalu_ativo():
            log.info("[4/4] Magalu inativo (MAGALU_VITRINE ausente) — nao sera iniciado.")
            return None
    except Exception as erro:
        log.warning("[4/4] Nao foi possivel checar o Magalu: %s", erro)
        return None

    log.info("[4/4] Iniciando rastreador Magalu (intervalo aleatório ~20 min)…")
    magalu_log_path = os.path.join(BASE, "data", "rastreador_magalu.log")
    cmd_mg = [
        sys.executable, os.path.join(BASE, "rastreador_magalu.py"),
        "--random", "--loop-min", "18", "--loop-max", "22",
    ]
    log_mg = open(magalu_log_path, "a", encoding="utf-8")
    proc_mg = subprocess.Popen(cmd_mg, stdout=log_mg, stderr=log_mg, cwd=BASE, **_SEM_JANELA)
    with open(os.path.join(BASE, "data", "rastreador_magalu.pid"), "w") as f:
        f.write(str(proc_mg.pid))
    log.info("[4/4] Rastreador Magalu PID=%d", proc_mg.pid)
    return proc_mg


def _iniciar_ferramentas():
    """Sobe a campanha de ferramentas — mesma lógica de isolamento dos outros."""
    log.info("[4/4] Iniciando campanha de ferramentas (a cada 15 min)…")
    ferr_log_path = os.path.join(BASE, "data", "campanha_ferramentas.log")
    cmd_ferr = [
        sys.executable, os.path.join(BASE, "campanha_ferramentas.py"),
        "--loop", "15",
    ]
    log_ferr = open(ferr_log_path, "a", encoding="utf-8")
    proc_ferr = subprocess.Popen(cmd_ferr, stdout=log_ferr, stderr=log_ferr, cwd=BASE, **_SEM_JANELA)
    with open(os.path.join(BASE, "data", "campanha_ferramentas.pid"), "w") as f:
        f.write(str(proc_ferr.pid))
    log.info("[4/4] Campanha de ferramentas PID=%d", proc_ferr.pid)
    return proc_ferr


def _iniciar_fila_whatsapp():
    """Sobe o consumidor da fila de WhatsApp (intervalo aleatório 30-45min)."""
    log.info("[4/4] Iniciando fila de envio WhatsApp (intervalo aleatório 30-45min)…")
    wa_log_path = os.path.join(BASE, "data", "whatsapp_queue_sender.log")
    cmd_wa = [sys.executable, os.path.join(BASE, "whatsapp_queue_sender.py")]
    log_wa = open(wa_log_path, "a", encoding="utf-8")
    proc_wa = subprocess.Popen(cmd_wa, stdout=log_wa, stderr=log_wa, cwd=BASE, **_SEM_JANELA)
    with open(os.path.join(BASE, "data", "whatsapp_queue_sender.pid"), "w") as f:
        f.write(str(proc_wa.pid))
    log.info("[4/4] Fila de WhatsApp PID=%d", proc_wa.pid)
    return proc_wa


def etapa_4_iniciar_rastreador() -> tuple:
    """Sobe rastreadores ML, Amazon, campanha de ferramentas e fila de WhatsApp em paralelo."""
    proc_ml = _iniciar_ml()
    proc_az = _iniciar_amazon()
    proc_mg = _iniciar_magalu()
    proc_ferr = _iniciar_ferramentas()
    proc_wa = _iniciar_fila_whatsapp()
    return proc_ml, proc_az, proc_mg, proc_ferr, proc_wa


class _Tracker:
    """Estado de monitoramento de um processo (ML, Amazon, campanha, ...)."""
    def __init__(self, nome: str, iniciar_fn, proc):
        self.nome = nome
        self.iniciar_fn = iniciar_fn
        self.proc = proc
        self.falhas = 0
        self.proximo_retry: float | None = None
        self.ultima_falha: float | None = None  # time.monotonic() da última queda
        self.desistiu = proc is None


def monitorar(procs) -> None:
    """Reinicia processos que caírem, mantendo os outros vivos.

    Generalizado para N processos (antes eram 2 blocos idênticos hardcoded
    pra ML/Amazon — a campanha de ferramentas entrando como terceiro tornou
    a duplicação insustentável). Duas correções mantidas da versão anterior:
    1. Reinício não bloqueia mais o loop inteiro — cada processo tem seu
       próprio "retry agendado" (timestamp), checado em polls curtos de 8s,
       então uma espera de um nunca atrasa a detecção/reinício dos outros.
    2. O contador de falhas reseta depois de um tempo estável sem quedas,
       em vez de acumular falhas esporádicas e não relacionadas ao longo de
       semanas até desistir de um processo saudável.
    """
    # procs pode ser um Popen único (legacy) ou tupla (ml, amazon, ferramentas, fila_wa)
    # Tolerante ao tamanho da tupla: a entrada do Magalu mudou a aridade de
    # 4 para 5, e um desempacotamento fixo quebraria um startup.py antigo
    # chamando um monitorar() novo (ou o contrario) com ValueError — em
    # producao isso e o supervisor inteiro morrendo, nao um aviso.
    if isinstance(procs, tuple):
        lista = list(procs) + [None] * (5 - len(procs))
        proc_ml, proc_az, proc_mg, proc_ferr, proc_wa = lista[:5]
    else:
        proc_ml = procs
        proc_az = proc_mg = proc_ferr = proc_wa = None

    RESET_APOS_SEGUNDOS = 2 * 60 * 60  # 2h estável reseta o contador de falhas

    trackers = [
        _Tracker("ML", _iniciar_ml, proc_ml),
        _Tracker("Amazon", _iniciar_amazon, proc_az),
        _Tracker("Magalu", _iniciar_magalu, proc_mg),
        _Tracker("Campanha Ferramentas", _iniciar_ferramentas, proc_ferr),
        _Tracker("Fila WhatsApp", _iniciar_fila_whatsapp, proc_wa),
    ]

    log.info("Sistema em produção — rastreadores + healthcheck ativos.")

    while True:
        time.sleep(8)  # poll curto — uma espera de retry nunca bloqueia os outros
        agora = time.monotonic()

        for t in trackers:
            if t.proc and t.proc.poll() is not None:
                t.falhas += 1
                t.ultima_falha = agora
                log.warning("%s caiu (código %s, falha %d/3)",
                            t.nome, t.proc.returncode, t.falhas)
                t.proc = None  # marca "caído, aguardando retry" — evita recontar no próximo poll
                if t.falhas < 3:
                    espera = min(30 * (2 ** (t.falhas - 1)), 300)
                    t.proximo_retry = agora + espera
                    log.info("Reiniciando %s em %ds…", t.nome, espera)
                else:
                    log.error("%s falhou %dx — desistindo dele", t.nome, t.falhas)
                    t.desistiu = True
            elif t.proximo_retry and agora >= t.proximo_retry:
                # Só reinicia ESTE processo — reiniciar todos juntos geraria
                # instâncias extras desnecessárias dos outros a cada queda
                # isolada, e sobrescreveria os .pid deles com PIDs órfãos.
                # try/except: se o próprio restart falhar (log travado, disco
                # cheio, WinError transitório), não pode derrubar o loop
                # inteiro e tirar supervisão dos OUTROS processos saudáveis —
                # trata como mais uma falha, reaproveitando o mesmo backoff.
                try:
                    t.proc = t.iniciar_fn()
                    t.proximo_retry = None
                except Exception as e:
                    t.falhas += 1
                    t.ultima_falha = agora
                    log.error("Falha ao reiniciar %s (tentativa %d/3): %s", t.nome, t.falhas, e)
                    if t.falhas < 3:
                        espera = min(30 * (2 ** (t.falhas - 1)), 300)
                        t.proximo_retry = agora + espera
                        log.info("Nova tentativa de %s em %ds…", t.nome, espera)
                    else:
                        log.error("%s falhou %dx ao reiniciar — desistindo dele", t.nome, t.falhas)
                        t.desistiu = True
                        t.proximo_retry = None
            elif t.proc and t.falhas > 0 and t.ultima_falha is not None \
                    and (agora - t.ultima_falha) > RESET_APOS_SEGUNDOS:
                log.info("%s estável há %.0fh — resetando contador de falhas (%d → 0)",
                          t.nome, RESET_APOS_SEGUNDOS / 3600, t.falhas)
                t.falhas = 0
                t.ultima_falha = None

        if all(t.desistiu for t in trackers):
            log.error("Todos os processos monitorados morreram — encerrando startup")
            break


def main() -> None:
    log.info("=" * 60)
    log.info("BOT OFERTAS — inicialização sequencial (WhatsApp Desktop nativo)")
    log.info("=" * 60)

    if _rastreador_ja_rodando():
        log.info("Rastreador já em execução — nada a fazer.")
        return

    if not etapa_1_validar_config():
        log.error("Configuração inválida. Corrija .env antes de continuar.")
        sys.exit(1)

    etapa_2_verificar_whatsapp_desktop()
    etapa_3_healthcheck()
    proc = etapa_4_iniciar_rastreador()
    monitorar(proc)


if __name__ == "__main__":
    main()
