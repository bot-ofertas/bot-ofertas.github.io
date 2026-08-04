# -*- coding: utf-8 -*-
"""
VERIFICAÇÃO DIÁRIA — roda às 01:00, antes do shutdown das 02:00.

Gera um relatório consolidado de saúde do sistema (processos vivos,
volume de postagens ML/Amazon/Ferramentas nas últimas 24h, erros
recentes) e envia por Telegram. Reaproveita core.monitor.verificar_e_alertar()
para os alertas reativos já existentes (bot parado, taxa de afiliado
baixa, etc.) e soma um resumo diário que não depende de threshold —
ou seja, chega todo dia, mesmo sem problema, servindo de "prova de vida".

Registrado como tarefa do Windows (BotOfertas-VerificacaoDiaria), sem
-StartWhenAvailable (mesma regra do shutdown/wake — ver aguardar_e_desligar.ps1).

Uso:
    python verificacao_diaria.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import datetime
import os
import sqlite3

from dotenv import load_dotenv

import core.database as db
from core.monitor import verificar_saude, verificar_e_alertar, enviar_alerta_telegram

load_dotenv()

TOKEN = os.getenv("TOKEN_TELEGRAM", "")
CHAT_ID = os.getenv("CHAT_ID_TELEGRAM", "")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bot_ofertas.db")

_PROCESSOS = [
    ("ML", "rastreador.pid", "rastreador.py"),
    ("Amazon", "rastreador_amazon.pid", "rastreador_amazon.py"),
    ("Campanha Ferramentas", "campanha_ferramentas.pid", "campanha_ferramentas.py"),
]


def _processo_vivo(pid_file: str, marcador: str) -> bool:
    """Confere se o PID salvo em data/<pid_file> ainda corresponde a um
    processo Python vivo rodando o script esperado (evita falso-positivo
    se o PID foi reciclado pelo Windows para outro programa)."""
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", pid_file)
    if not os.path.exists(caminho):
        return False
    try:
        with open(caminho) as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        return False
    try:
        import psutil  # noqa: PLC0415
        p = psutil.Process(pid)
        cmd = " ".join(p.cmdline())
        return marcador in cmd
    except Exception:
        return False


def _contagem_24h() -> dict:
    """Posts publicados por provedor de afiliado nas últimas 24h."""
    resultado = {"mercadolivre": 0, "amazon": 0}
    if not os.path.exists(DB_PATH):
        return resultado
    desde = (datetime.datetime.now() - datetime.timedelta(hours=24)).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT affiliate_provider, COUNT(*) FROM produtos "
                "WHERE enviado_em >= ? AND affiliate_provider IS NOT NULL "
                "GROUP BY affiliate_provider",
                (desde,),
            )
            for provider, n in cur.fetchall():
                resultado[provider] = n
    except sqlite3.OperationalError:
        pass
    return resultado


def _erros_24h_por_tipo(limite: int = 5) -> list[tuple[str, int]]:
    if not os.path.exists(DB_PATH):
        return []
    desde = (datetime.datetime.now() - datetime.timedelta(hours=24)).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT tipo, COUNT(*) FROM erros_log WHERE ocorrido_em >= ? "
                "GROUP BY tipo ORDER BY COUNT(*) DESC LIMIT ?",
                (desde, limite),
            )
            return cur.fetchall()
    except sqlite3.OperationalError:
        return []


def gerar_relatorio() -> str:
    saude = verificar_saude()
    contagem = _contagem_24h()
    erros = _erros_24h_por_tipo()
    agora = datetime.datetime.now()

    linhas = [
        f"<b>Verificação diária — {agora.strftime('%d/%m/%Y %H:%M')}</b>",
        "",
        "<b>Processos:</b>",
    ]
    for nome, pid_file, marcador in _PROCESSOS:
        vivo = _processo_vivo(pid_file, marcador)
        linhas.append(f"  {'✅' if vivo else '❌'} {nome}")

    linhas += [
        "",
        "<b>Postagens (24h):</b>",
        f"  Mercado Livre: {contagem.get('mercadolivre', 0)}",
        f"  Amazon: {contagem.get('amazon', 0)}",
        "",
        f"<b>Taxa de afiliado:</b> {saude['affiliate_taxa']}%",
        f"<b>Erros (2h):</b> {saude['erros_recentes']}",
    ]

    if erros:
        linhas.append("")
        linhas.append("<b>Erros (24h) por tipo:</b>")
        for tipo, n in erros:
            linhas.append(f"  {tipo}: {n}")

    return "\n".join(linhas)


def main() -> None:
    db.inicializar()

    relatorio = gerar_relatorio()
    print(relatorio.replace("<b>", "").replace("</b>", ""))
    enviar_alerta_telegram(relatorio, TOKEN, CHAT_ID)

    # Alertas reativos existentes (bot parado, taxa baixa, execução antiga, etc.)
    verificar_e_alertar(token=TOKEN, chat_id=CHAT_ID)

    # Atualiza o arquivo de problemas na área de trabalho (pedido do
    # Daniel em 2026-08-04) — best-effort, nunca derruba a verificação
    # diária se a área de trabalho não estiver acessível por algum motivo.
    try:
        from gerar_relatorio_problemas import main as gerar_relatorio_desktop
        gerar_relatorio_desktop()
    except Exception as e:
        print(f"Não consegui atualizar o relatório da área de trabalho: {e}")


if __name__ == "__main__":
    main()
