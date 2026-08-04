# -*- coding: utf-8 -*-
"""
GERADOR DE RELATÓRIO DE PROBLEMAS — arquivo de texto simples na área de
trabalho, pra consulta rápida sem precisar abrir código/terminal.

Lê os erros registrados em erros_log (últimas 24h) + o status geral de
saúde do sistema, e escreve/atualiza um .txt legível em:
    C:\\Users\\Daniel\\Desktop\\problemas de execucao.txt

Uso:
    python gerar_relatorio_problemas.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import datetime
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bot_ofertas.db")
DESKTOP_PATH = os.path.join(os.path.expanduser("~"), "Desktop")
ARQUIVO_SAIDA = os.path.join(DESKTOP_PATH, "problemas de execucao.txt")


def _erros_24h() -> list[tuple]:
    if not os.path.exists(DB_PATH):
        return []
    desde = (datetime.datetime.now() - datetime.timedelta(hours=24)).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT tipo, mensagem, produto_id, ocorrido_em FROM erros_log "
                "WHERE ocorrido_em >= ? ORDER BY ocorrido_em DESC",
                (desde,),
            )
            return cur.fetchall()
    except sqlite3.OperationalError:
        return []


def _resumo_por_tipo(erros: list[tuple]) -> dict[str, int]:
    resumo: dict[str, int] = {}
    for tipo, *_ in erros:
        resumo[tipo] = resumo.get(tipo, 0) + 1
    return resumo


def gerar_relatorio() -> str:
    from core.monitor import verificar_saude  # noqa: PLC0415

    agora = datetime.datetime.now()
    saude = verificar_saude()
    erros = _erros_24h()
    resumo = _resumo_por_tipo(erros)

    linhas = [
        "=" * 70,
        f"RELATÓRIO DE PROBLEMAS — Bot Ofertas — {agora.strftime('%d/%m/%Y %H:%M')}",
        "=" * 70,
        "",
        "STATUS GERAL:",
        f"  Bot rodando: {'SIM' if saude['bot_rodando'] else 'NÃO — VERIFICAR'}",
        f"  Banco de dados OK: {'SIM' if saude['db_ok'] else 'NÃO — VERIFICAR'}",
        f"  Taxa de sucesso de link de afiliado: {saude['affiliate_taxa']}%",
        f"  Última execução registrada: {saude['ultima_execucao'] or 'nenhuma'}",
        "",
    ]

    if not erros:
        linhas.append("Nenhum erro registrado nas últimas 24 horas. Sistema limpo.")
    else:
        linhas.append(f"ERROS NAS ÚLTIMAS 24H: {len(erros)} no total")
        linhas.append("")
        linhas.append("Resumo por tipo:")
        for tipo, n in sorted(resumo.items(), key=lambda x: -x[1]):
            linhas.append(f"  - {tipo}: {n}x")
        linhas.append("")
        linhas.append("-" * 70)
        linhas.append("DETALHES (mais recentes primeiro, até 40):")
        linhas.append("-" * 70)
        for tipo, mensagem, produto_id, ocorrido_em in erros[:40]:
            hora = (ocorrido_em or "")[:19].replace("T", " ")
            linhas.append(f"[{hora}] {tipo}")
            linhas.append(f"    {mensagem}")
            if produto_id:
                linhas.append(f"    produto: {produto_id}")
            linhas.append("")

    linhas.append("=" * 70)
    linhas.append("Esse arquivo é atualizado automaticamente pelo bot. Pode fechar e")
    linhas.append("reabrir quando quiser conferir o estado mais recente.")
    linhas.append("=" * 70)

    return "\n".join(linhas)


def main() -> None:
    conteudo = gerar_relatorio()
    os.makedirs(DESKTOP_PATH, exist_ok=True)
    with open(ARQUIVO_SAIDA, "w", encoding="utf-8") as f:
        f.write(conteudo)
    print(f"Relatório salvo em: {ARQUIVO_SAIDA}")


if __name__ == "__main__":
    main()
