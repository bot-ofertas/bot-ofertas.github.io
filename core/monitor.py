"""
Modulo de monitoramento e alertas para o bot de ofertas.
Verifica saude do sistema e envia alertas via Telegram.
"""

import os
import json
import logging
import sqlite3
import subprocess
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("monitor")

_ultimo_alerta_em: dict = {}

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "bot_ofertas.db")
DASHBOARD_URL = "http://localhost:5000"


def verificar_saude() -> dict:
    """
    Verifica o estado de saude do bot e retorna um dicionario com os indicadores.

    Returns:
        dict com campos:
            - bot_rodando: bool
            - dashboard_ok: bool
            - db_ok: bool
            - ultima_execucao: str
            - erros_recentes: int
            - affiliate_taxa: float
    """
    resultado = {
        "bot_rodando": False,
        "dashboard_ok": False,
        "db_ok": False,
        "ultima_execucao": "",
        "erros_recentes": 0,
        "affiliate_taxa": 0.0,
    }

    # Verifica se ha processo python rastreador.py em execucao
    try:
        saida = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode("utf-8", errors="ignore")
        # Tenta tambem listar argumentos via wmic para confirmar rastreador.py
        try:
            wmic_saida = subprocess.check_output(
                ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine", "/FORMAT:CSV"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).decode("utf-8", errors="ignore")
            resultado["bot_rodando"] = "rastreador.py" in wmic_saida
        except Exception:
            # Fallback: se python.exe esta rodando, assume que pode ser o bot
            resultado["bot_rodando"] = "python.exe" in saida.lower()
    except Exception:
        resultado["bot_rodando"] = False

    # Verifica se o dashboard responde
    try:
        resposta = requests.get(DASHBOARD_URL, timeout=3)
        resultado["dashboard_ok"] = resposta.status_code == 200
    except Exception:
        resultado["dashboard_ok"] = False

    # Verifica se o banco existe e foi modificado nas ultimas 2 horas
    db_path = os.path.abspath(DB_PATH)
    if os.path.exists(db_path):
        mtime = os.path.getmtime(db_path)
        dt_modificacao = datetime.datetime.fromtimestamp(mtime)
        duas_horas_atras = datetime.datetime.now() - datetime.timedelta(hours=2)
        resultado["db_ok"] = dt_modificacao >= duas_horas_atras
    else:
        resultado["db_ok"] = False

    # Consulta banco SQLite para ultima_execucao, erros_recentes e affiliate_taxa
    if os.path.exists(db_path):
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()

                # Ultima execucao do bot (tabela: execucoes, campo: iniciado_em)
                try:
                    cursor.execute(
                        "SELECT MAX(iniciado_em) FROM execucoes"
                    )
                    linha = cursor.fetchone()
                    resultado["ultima_execucao"] = linha[0] if linha and linha[0] else ""
                except sqlite3.OperationalError:
                    resultado["ultima_execucao"] = ""

                # Erros nas ultimas 2 horas (tabela: erros_log, campo: ocorrido_em)
                try:
                    duas_horas_atras_str = (
                        datetime.datetime.now() - datetime.timedelta(hours=2)
                    ).isoformat()
                    cursor.execute(
                        "SELECT COUNT(*) FROM erros_log WHERE ocorrido_em >= ?",
                        (duas_horas_atras_str,),
                    )
                    linha = cursor.fetchone()
                    resultado["erros_recentes"] = int(linha[0]) if linha and linha[0] else 0
                except sqlite3.OperationalError:
                    resultado["erros_recentes"] = 0

                # Taxa de sucesso de links de afiliado (tabela: produtos, campo: affiliate_status)
                try:
                    cursor.execute(
                        "SELECT COUNT(*) FROM produtos WHERE affiliate_status='ok'"
                    )
                    linha_sucesso = cursor.fetchone()
                    cursor.execute(
                        "SELECT COUNT(*) FROM produtos WHERE affiliate_status IN ('ok','erro')"
                    )
                    linha_total = cursor.fetchone()
                    total = int(linha_total[0]) if linha_total and linha_total[0] else 0
                    sucesso = int(linha_sucesso[0]) if linha_sucesso and linha_sucesso[0] else 0
                    if total > 0:
                        resultado["affiliate_taxa"] = round((sucesso / total) * 100, 2)
                    else:
                        resultado["affiliate_taxa"] = 100.0
                except sqlite3.OperationalError:
                    resultado["affiliate_taxa"] = 100.0
        except Exception as e:
            log.warning("verificar_saude: falha ao consultar %s: %s", db_path, e)

    return resultado


def enviar_alerta_telegram(mensagem: str, token: str, chat_id: str) -> None:
    """
    Envia alerta via API do Telegram usando requests diretamente.
    Silencia erros de rede.

    Args:
        mensagem: Texto do alerta a ser enviado.
        token: Token do bot do Telegram.
        chat_id: ID do chat destino.
    """
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": mensagem,
        "parse_mode": "HTML",
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass


def verificar_e_alertar(token: str = "", chat_id: str = "") -> None:
    """
    Executa verificacao de saude e envia alertas conforme necessario.
    Evita spam: so alerta 1x por hora para o mesmo problema.

    Args:
        token: Token do bot do Telegram (usa TOKEN_TELEGRAM do .env se vazio).
        chat_id: Chat ID do Telegram (usa CHAT_ID_TELEGRAM do .env se vazio).
    """
    global _ultimo_alerta_em

    if not token:
        token = os.getenv("TOKEN_TELEGRAM", "")
    if not chat_id:
        chat_id = os.getenv("CHAT_ID_TELEGRAM", "")

    saude = verificar_saude()
    agora = datetime.datetime.now()
    uma_hora_atras = agora - datetime.timedelta(hours=1)

    def pode_alertar(chave: str) -> bool:
        ultimo = _ultimo_alerta_em.get(chave)
        if ultimo is None or ultimo < uma_hora_atras:
            _ultimo_alerta_em[chave] = agora
            return True
        return False

    # Bot nao esta rodando
    if not saude["bot_rodando"]:
        if pode_alertar("bot_parado"):
            enviar_alerta_telegram(
                "[ALERTA] Bot de ofertas nao esta em execucao!\n"
                f"Horario: {agora.strftime('%d/%m/%Y %H:%M:%S')}",
                token,
                chat_id,
            )

    # Muitos erros recentes
    if saude["erros_recentes"] > 10:
        if pode_alertar("erros_recentes"):
            enviar_alerta_telegram(
                f"[ALERTA] {saude['erros_recentes']} erros registrados nas ultimas 2 horas!\n"
                f"Horario: {agora.strftime('%d/%m/%Y %H:%M:%S')}",
                token,
                chat_id,
            )

    # Taxa de afiliado baixa
    if saude["affiliate_taxa"] < 50.0:
        if pode_alertar("affiliate_taxa_baixa"):
            enviar_alerta_telegram(
                f"[AVISO] Taxa de sucesso de links de afiliado esta baixa: {saude['affiliate_taxa']}%\n"
                f"Horario: {agora.strftime('%d/%m/%Y %H:%M:%S')}",
                token,
                chat_id,
            )

    # Ultima execucao ha mais de 3 horas
    if saude["ultima_execucao"]:
        try:
            # Tenta parsear varios formatos de timestamp
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
                try:
                    dt_ultima = datetime.datetime.strptime(
                        str(saude["ultima_execucao"])[:19], fmt[:len(fmt)]
                    )
                    break
                except ValueError:
                    continue
            else:
                dt_ultima = None

            if dt_ultima:
                tres_horas_atras = agora - datetime.timedelta(hours=3)
                if dt_ultima < tres_horas_atras:
                    if pode_alertar("ultima_execucao_antiga"):
                        enviar_alerta_telegram(
                            "[ALERTA] Bot nao executa ha mais de 3 horas!\n"
                            f"Ultima execucao: {saude['ultima_execucao']}\n"
                            f"Horario atual: {agora.strftime('%d/%m/%Y %H:%M:%S')}",
                            token,
                            chat_id,
                        )
        except Exception:
            pass


if __name__ == "__main__":
    token_env = os.getenv("TOKEN_TELEGRAM", "")
    chat_id_env = os.getenv("CHAT_ID_TELEGRAM", "")
    verificar_e_alertar(token=token_env, chat_id=chat_id_env)
