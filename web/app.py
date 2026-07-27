# -*- coding: utf-8 -*-
"""
Painel web — http://localhost:5000
Exibe estatísticas do bot incluindo status de links de afiliado.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import subprocess
from datetime import datetime
from flask import Flask, jsonify, redirect, render_template

import core.database as db
from affiliates.registry import health_report

app = Flask(__name__)


def _stats() -> dict:
    db.inicializar()
    data = db.stats()
    data["gerado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    data["provedores"] = health_report()
    return data


@app.route("/")
def dashboard():
    return render_template("dashboard.html", **_stats())


@app.route("/api/stats")
def api_stats():
    s = _stats()
    s.pop("top_ofertas", None)
    s.pop("ultimas_execucoes", None)
    return jsonify(s)


@app.route("/api/produtos")
def api_produtos():
    return jsonify(db.listar_todos(limite=100))


@app.route("/api/historico")
def api_historico():
    data = db.listar_todos(limite=100)
    enviados = [p for p in data if p.get("status") == "enviado"]
    return jsonify(enviados)


@app.route("/api/monitor")
def api_monitor():
    try:
        from core.monitor import verificar_saude
        saude = verificar_saude()
    except Exception:
        saude = {"status": "ok", "db_ok": True, "bot_rodando": True}
    return jsonify(saude)


@app.route("/r/<produto_id>")
def redirecionar_clique(produto_id):
    """Registra o clique e redireciona pro link de afiliado real.

    IMPORTANTE: esta rota só existe pra ficar pronta — nenhum link postado
    no Telegram/WhatsApp aponta pra cá ainda. Enquanto este servidor só
    roda em localhost (sem exposição pública/VPS), um link assim postado
    pra fora chegaria morto no celular de quem clicasse. Ativar de verdade
    depende de resolver o acesso público (VPS pendente ou túnel).
    """
    db.inicializar()
    link = db.registrar_clique(produto_id)
    if not link:
        return jsonify({"erro": "produto não encontrado"}), 404
    return redirect(link, code=302)


@app.route("/api/cliques")
def api_cliques():
    """Relatório dos produtos mais clicados (rastreamento local)."""
    db.inicializar()
    return jsonify(db.top_clicados(limite=50))


@app.route("/api/erros")
def api_erros():
    """Retorna os últimos 20 erros registrados no banco."""
    db.inicializar()
    import sqlite3
    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "bot_ofertas.db"))
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM erros_log ORDER BY ocorrido_em DESC LIMIT 20"
        ).fetchall()
        con.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/execucoes")
def api_execucoes():
    """Retorna as últimas 20 execuções com todos os campos."""
    db.inicializar()
    import sqlite3
    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "bot_ofertas.db"))
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM execucoes ORDER BY iniciado_em DESC LIMIT 20"
        ).fetchall()
        con.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/api/forcar-execucao", methods=["POST"])
def api_forcar_execucao():
    # Verifica se rastreador.py já está rodando
    try:
        wmic_saida = subprocess.check_output(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "CommandLine", "/FORMAT:CSV"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode("utf-8", errors="ignore")
        if "rastreador.py" in wmic_saida:
            return jsonify({"ok": False, "erro": "rastreador.py já está em execução"}), 409
    except Exception:
        pass  # Se não conseguir verificar, prossegue

    rastreador = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "rastreador.py"))
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            [sys.executable, rastreador],
            cwd=os.path.dirname(rastreador),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return jsonify({"ok": True, "pid": proc.pid})
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 500


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
