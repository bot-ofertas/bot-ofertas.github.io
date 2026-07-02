# -*- coding: utf-8 -*-
"""
STARTUP — Inicialização sequencial e resiliente do bot de ofertas.

Ordem correta (elimina ~99% dos erros de integração):

    1. Validar configurações (.env, TOKEN_TELEGRAM)
    2. Iniciar Chrome do bot com --remote-debugging-port=9222
    3. AGUARDAR a porta 9222 responder /json/version
    4. Iniciar watchdog (monitor contínuo do Chrome)
    5. Iniciar rastreador em loop (Telegram sempre, WhatsApp best-effort)

Regra de ouro: o Telegram NUNCA depende do WhatsApp. Se o Chrome cair, o
Telegram continua postando; o watchdog cuida de recuperar o WhatsApp em
background e reintegrar quando voltar.

Registrado como tarefa do Windows (BotOfertas-AutoStart) — roda no login.
"""
import logging
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)  # garante que core/ e integrations/ resolvem

LOG_PATH = os.path.join(BASE, "data", "rastreador_local.log")
PID_PATH = os.path.join(BASE, "data", "rastreador.pid")

os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(BASE, "data", "startup.log"), encoding="utf-8"),
    ],
)
log = logging.getLogger("startup")


def _rastreador_ja_rodando() -> bool:
    """Evita duplicata: True se já há um rastreador.py --loop em execução."""
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
    """Valida que o .env carrega e que TOKEN_TELEGRAM existe."""
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv(os.path.join(BASE, ".env"))
    except Exception as e:
        log.error("[1/5] Falha ao carregar .env: %s", e)
        return False

    token = os.getenv("TOKEN_TELEGRAM")
    if not token:
        log.error("[1/5] TOKEN_TELEGRAM ausente no .env — Telegram não vai funcionar.")
        return False
    log.info("[1/5] Config OK — TOKEN_TELEGRAM presente.")
    return True


def etapa_2_iniciar_chrome() -> bool:
    """Inicia o Chrome do bot e AGUARDA a porta 9222 responder de fato."""
    from core.chrome_manager import garantir_chrome_pronto  # noqa: PLC0415
    log.info("[2/5] Iniciando Chrome do bot e aguardando porta 9222…")
    if garantir_chrome_pronto(timeout=60):
        log.info("[2/5] Chrome OK — CDP respondendo em 127.0.0.1:9222.")
        return True
    log.warning("[2/5] Chrome não subiu — WhatsApp ficará indisponível, "
                "mas Telegram vai continuar postando.")
    return False


def etapa_3_watchdog() -> None:
    """Ativa watchdog + healthcheck (monitoramento contínuo)."""
    try:
        from core.watchdog import iniciar_watchdog  # noqa: PLC0415
        iniciar_watchdog()
        log.info("[3/5] Watchdog ativo — checa Chrome a cada 30s.")
    except Exception as e:
        log.warning("[3/5] Watchdog não subiu: %s (não crítico).", e)
    try:
        from core.healthcheck import iniciar_healthcheck  # noqa: PLC0415
        iniciar_healthcheck()
    except Exception as e:
        log.warning("[3/5] Healthcheck não subiu: %s (não crítico).", e)


def etapa_4_iniciar_rastreador() -> subprocess.Popen:
    """Sobe o rastreador em loop (Telegram sempre; WhatsApp best-effort)."""
    log.info("[4/5] Iniciando rastreador (loop 20 min)…")
    cmd = [sys.executable, os.path.join(BASE, "rastreador.py"), "--loop", "20"]
    log_f = open(LOG_PATH, "a", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=log_f, stderr=log_f, cwd=BASE)
    with open(PID_PATH, "w") as f:
        f.write(str(proc.pid))
    log.info("[4/5] Rastreador PID=%d — log em %s", proc.pid, LOG_PATH)
    return proc


def etapa_5_monitorar(proc: subprocess.Popen) -> None:
    """Aguarda o rastreador; se ele morrer, tenta reiniciar 3x com backoff."""
    log.info("[5/5] Sistema em produção. Watchdog + rastreador ativos.")
    falhas = 0
    while True:
        code = proc.wait()
        log.warning("Rastreador encerrou com código %s.", code)
        falhas += 1
        if falhas > 3:
            log.error("Rastreador falhou %d vezes seguidas — encerrando startup.", falhas)
            break
        espera = min(30 * (2 ** (falhas - 1)), 300)
        log.info("Reiniciando rastreador em %ds (tentativa %d)…", espera, falhas)
        time.sleep(espera)
        proc = etapa_4_iniciar_rastreador()


def main() -> None:
    log.info("=" * 60)
    log.info("BOT OFERTAS — inicialização sequencial")
    log.info("=" * 60)

    if _rastreador_ja_rodando():
        log.info("Rastreador já em execução — nada a fazer.")
        return

    if not etapa_1_validar_config():
        log.error("Configuração inválida. Corrija .env antes de continuar.")
        sys.exit(1)

    # Chrome é best-effort: Telegram funciona sem ele
    etapa_2_iniciar_chrome()
    etapa_3_watchdog()
    proc = etapa_4_iniciar_rastreador()
    etapa_5_monitorar(proc)


if __name__ == "__main__":
    main()
