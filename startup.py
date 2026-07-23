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
    try:
        import psutil  # noqa: PLC0415
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                cl = " ".join(p.info.get("cmdline") or [])
                nome = (p.info.get("name") or "").lower()
                if "rastreador.py" in cl and "--loop" in cl and "python" in nome:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass
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


def _iniciar_ml():
    """Sobe só o rastreador ML — usado no start inicial e em reinícios isolados
    (um crash do ML não pode gerar um processo Amazon extra desnecessário)."""
    log.info("[4/4] Iniciando rastreador ML (intervalo aleatório 30-45 min)…")
    cmd_ml = [
        sys.executable, os.path.join(BASE, "rastreador.py"),
        "--random", "--loop-min", "30", "--loop-max", "45",
    ]
    log_ml = open(LOG_PATH, "a", encoding="utf-8")
    proc_ml = subprocess.Popen(cmd_ml, stdout=log_ml, stderr=log_ml, cwd=BASE)
    with open(PID_PATH, "w") as f:
        f.write(str(proc_ml.pid))
    log.info("[4/4] Rastreador ML PID=%d", proc_ml.pid)
    return proc_ml


def _iniciar_amazon():
    """Sobe só o rastreador Amazon — mesma lógica de isolamento do _iniciar_ml."""
    log.info("[4/4] Iniciando rastreador Amazon (intervalo 45-75 min)…")
    amazon_log_path = os.path.join(BASE, "data", "rastreador_amazon.log")
    cmd_az = [
        sys.executable, os.path.join(BASE, "rastreador_amazon.py"),
        "--random", "--loop-min", "45", "--loop-max", "75",
    ]
    log_az = open(amazon_log_path, "a", encoding="utf-8")
    proc_az = subprocess.Popen(cmd_az, stdout=log_az, stderr=log_az, cwd=BASE)
    with open(os.path.join(BASE, "data", "rastreador_amazon.pid"), "w") as f:
        f.write(str(proc_az.pid))
    log.info("[4/4] Rastreador Amazon PID=%d", proc_az.pid)
    return proc_az


def etapa_4_iniciar_rastreador() -> tuple:
    """Sobe rastreadores ML e Amazon em paralelo (intervalos aleatórios)."""
    proc_ml = _iniciar_ml()
    proc_az = _iniciar_amazon()
    return proc_ml, proc_az


def monitorar(procs) -> None:
    """Reinicia rastreadores que caírem, mantendo os outros vivos.

    Duas correções sobre a versão anterior:
    1. O reinício usava time.sleep(espera) inline (até 300s) ANTES de
       reiniciar — isso bloqueava o loop inteiro, então se o Amazon caísse
       durante a espera do ML (ou vice-versa), a queda nem era detectada até
       o sleep do outro terminar. Agora cada um tem seu próprio "retry
       agendado" (timestamp), checado em polls curtos de 8s — uma espera
       nunca bloqueia a detecção/reinício do outro.
    2. falhas_ml/falhas_az nunca resetavam — falhas esporádicas e não
       relacionadas ao longo de semanas de uptime acumulavam até bater 3 e
       desistir permanentemente de um tracker saudável. Agora reseta o
       contador depois de um tempo estável sem quedas.
    """
    # procs pode ser um único Popen (legacy) ou tupla (ml, amazon)
    if isinstance(procs, tuple):
        proc_ml, proc_az = procs
    else:
        proc_ml, proc_az = procs, None

    RESET_APOS_SEGUNDOS = 2 * 60 * 60  # 2h estável reseta o contador de falhas

    log.info("Sistema em produção — rastreadores + healthcheck ativos.")
    falhas_ml = falhas_az = 0
    proximo_retry_ml = proximo_retry_az = None
    ultima_falha_ml = ultima_falha_az = None  # time.monotonic() da última queda
    desistiu_ml = proc_ml is None
    desistiu_az = proc_az is None

    while True:
        time.sleep(8)  # poll curto — uma espera de retry nunca bloqueia o outro
        agora = time.monotonic()

        if proc_ml and proc_ml.poll() is not None:
            falhas_ml += 1
            ultima_falha_ml = agora
            log.warning("Rastreador ML caiu (código %s, falha %d/3)",
                        proc_ml.returncode, falhas_ml)
            proc_ml = None  # marca "caído, aguardando retry" — evita recontar no próximo poll
            if falhas_ml <= 3:
                espera = min(30 * (2 ** (falhas_ml - 1)), 300)
                proximo_retry_ml = agora + espera
                log.info("Reiniciando ML em %ds…", espera)
            else:
                log.error("ML falhou 3x — desistindo dele")
                desistiu_ml = True
        elif proximo_retry_ml and agora >= proximo_retry_ml:
            # Só reinicia o ML — reiniciar os dois junto (via
            # etapa_4_iniciar_rastreador) gera um Amazon extra
            # desnecessário toda vez que só o ML cai, e sobrescreve
            # data/rastreador_amazon.pid com o PID desse processo órfão.
            proc_ml = _iniciar_ml()
            proximo_retry_ml = None
        elif proc_ml and falhas_ml > 0 and ultima_falha_ml is not None \
                and (agora - ultima_falha_ml) > RESET_APOS_SEGUNDOS:
            log.info("ML estável há %.0fh — resetando contador de falhas (%d → 0)",
                      RESET_APOS_SEGUNDOS / 3600, falhas_ml)
            falhas_ml = 0
            ultima_falha_ml = None

        if proc_az and proc_az.poll() is not None:
            falhas_az += 1
            ultima_falha_az = agora
            log.warning("Rastreador Amazon caiu (código %s, falha %d/3)",
                        proc_az.returncode, falhas_az)
            proc_az = None
            if falhas_az <= 3:
                espera = min(30 * (2 ** (falhas_az - 1)), 300)
                proximo_retry_az = agora + espera
                log.info("Reiniciando Amazon em %ds…", espera)
            else:
                log.error("Amazon falhou 3x — desistindo dele")
                desistiu_az = True
        elif proximo_retry_az and agora >= proximo_retry_az:
            proc_az = _iniciar_amazon()
            proximo_retry_az = None
        elif proc_az and falhas_az > 0 and ultima_falha_az is not None \
                and (agora - ultima_falha_az) > RESET_APOS_SEGUNDOS:
            log.info("Amazon estável há %.0fh — resetando contador de falhas (%d → 0)",
                      RESET_APOS_SEGUNDOS / 3600, falhas_az)
            falhas_az = 0
            ultima_falha_az = None

        if desistiu_ml and desistiu_az:
            log.error("Todos rastreadores mortos — encerrando startup")
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
