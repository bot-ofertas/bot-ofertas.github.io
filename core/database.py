# -*- coding: utf-8 -*-
"""
Camada de banco de dados SQLite.
Substitui os arquivos JSON (produtos.json, historico.json).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "bot_ofertas.db")

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS produtos (
    id                  TEXT PRIMARY KEY,
    titulo              TEXT NOT NULL,
    preco               REAL,
    preco_original      REAL,
    desconto_pct        REAL DEFAULT 0,
    foto                TEXT,
    categoria           TEXT DEFAULT 'geral',
    canal               TEXT DEFAULT 'geral',
    status              TEXT DEFAULT 'pendente',
    score               INTEGER DEFAULT 0,

    -- Afiliado
    affiliate_provider  TEXT,
    affiliate_link      TEXT,
    affiliate_status    TEXT DEFAULT 'pending',
    affiliate_created_at TEXT,

    -- Validação
    last_validation     TEXT,
    validation_ok       INTEGER DEFAULT 0,

    -- Rastreamento (futuro)
    clicks              INTEGER DEFAULT 0,
    commission_status   TEXT DEFAULT 'unknown',

    -- Conteúdo extra
    cupom               TEXT,

    -- Datas
    adicionado_em       TEXT NOT NULL,
    enviado_em          TEXT
);

CREATE TABLE IF NOT EXISTS execucoes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    iniciado_em             TEXT NOT NULL,
    concluido_em            TEXT,
    produtos_encontrados    INTEGER DEFAULT 0,
    links_gerados           INTEGER DEFAULT 0,
    links_falharam          INTEGER DEFAULT 0,
    publicados              INTEGER DEFAULT 0,
    duplicatas              INTEGER DEFAULT 0,
    erros                   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS erros_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo        TEXT,
    mensagem    TEXT,
    produto_id  TEXT,
    ocorrido_em TEXT
);

CREATE INDEX IF NOT EXISTS idx_produtos_status ON produtos(status);
CREATE INDEX IF NOT EXISTS idx_produtos_adicionado ON produtos(adicionado_em);
CREATE INDEX IF NOT EXISTS idx_produtos_affiliate ON produtos(affiliate_status);
"""


def _ensure_dir():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)


@contextmanager
def _conn():
    _ensure_dir()
    con = sqlite3.connect(_DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def inicializar():
    """Cria as tabelas se não existirem."""
    _ensure_dir()
    with _conn() as con:
        con.executescript(_DDL)


# ── Produtos ──────────────────────────────────────────────────────────────────

def inserir_produto(p: dict) -> None:
    now = datetime.now().isoformat()
    with _conn() as con:
        # Adiciona coluna cupom se não existir (migração automática)
        cols = {r[1] for r in con.execute("PRAGMA table_info(produtos)").fetchall()}
        if "cupom" not in cols:
            con.execute("ALTER TABLE produtos ADD COLUMN cupom TEXT")
        con.execute("""
            INSERT OR IGNORE INTO produtos
                (id, titulo, preco, preco_original, desconto_pct, foto,
                 categoria, canal, status, score, cupom, adicionado_em)
            VALUES
                (:id, :titulo, :preco, :preco_original, :desconto_pct, :foto,
                 :categoria, :canal, :status, :score, :cupom, :adicionado_em)
        """, {
            "id":            p.get("id", f"p_{int(datetime.now().timestamp())}"),
            "titulo":        p.get("titulo", ""),
            "preco":         p.get("preco"),
            "preco_original": p.get("preco_original"),
            "desconto_pct":  p.get("desconto_pct", 0),
            "foto":          p.get("foto"),
            "categoria":     p.get("categoria", "geral"),
            "canal":         p.get("canal", "geral"),
            "status":        p.get("status", "pendente"),
            "score":         p.get("score", 0),
            "cupom":         p.get("cupom"),
            "adicionado_em": p.get("adicionado_em", now),
        })


def atualizar_afiliado(produto_id: str, provider: str, link: str, status: str = "ok") -> None:
    now = datetime.now().isoformat()
    with _conn() as con:
        con.execute("""
            UPDATE produtos
            SET affiliate_provider  = ?,
                affiliate_link      = ?,
                affiliate_status    = ?,
                affiliate_created_at = ?
            WHERE id = ?
        """, (provider, link, status, now, produto_id))


def marcar_enviado(produto_id: str) -> None:
    now = datetime.now().isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE produtos SET status='enviado', enviado_em=? WHERE id=?",
            (now, produto_id)
        )


def marcar_duplicata(produto_id: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE produtos SET status='duplicata' WHERE id=?",
            (produto_id,)
        )


def listar_pendentes(limite: int = 50) -> list[dict]:
    with _conn() as con:
        rows = con.execute("""
            SELECT * FROM produtos
            WHERE status = 'pendente'
            ORDER BY score DESC
            LIMIT ?
        """, (limite,)).fetchall()
    return [dict(r) for r in rows]


def listar_todos(limite: int = 200) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM produtos ORDER BY adicionado_em DESC LIMIT ?", (limite,)
        ).fetchall()
    return [dict(r) for r in rows]


def link_ja_existe(link: str) -> bool:
    """Verifica se um link (ou URL base sem parâmetros) já está no banco."""
    url_base = link.split("?")[0].rstrip("/")
    with _conn() as con:
        row = con.execute("""
            SELECT id FROM produtos
            WHERE affiliate_link LIKE ? OR affiliate_link LIKE ?
            LIMIT 1
        """, (f"{url_base}%", f"%{url_base}%")).fetchone()
    return row is not None


def stats() -> dict:
    with _conn() as con:
        total       = con.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
        enviados    = con.execute("SELECT COUNT(*) FROM produtos WHERE status='enviado'").fetchone()[0]
        pendentes   = con.execute("SELECT COUNT(*) FROM produtos WHERE status='pendente'").fetchone()[0]
        duplicatas  = con.execute("SELECT COUNT(*) FROM produtos WHERE status='duplicata'").fetchone()[0]
        score_med   = con.execute("SELECT AVG(score) FROM produtos WHERE score > 0").fetchone()[0] or 0
        afil_ok     = con.execute("SELECT COUNT(*) FROM produtos WHERE affiliate_status='ok'").fetchone()[0]
        afil_fail   = con.execute("SELECT COUNT(*) FROM produtos WHERE affiliate_status='erro'").fetchone()[0]
        afil_pend   = con.execute("SELECT COUNT(*) FROM produtos WHERE affiliate_status='pending'").fetchone()[0]
        top = con.execute("""
            SELECT * FROM produtos WHERE score > 0
            ORDER BY score DESC LIMIT 10
        """).fetchall()
        ultimas = con.execute("""
            SELECT * FROM execucoes ORDER BY iniciado_em DESC LIMIT 5
        """).fetchall()
    return {
        "total":            total,
        "enviados":         enviados,
        "pendentes":        pendentes,
        "duplicatas":       duplicatas,
        "score_medio":      int(score_med),
        "afiliado_ok":      afil_ok,
        "afiliado_falha":   afil_fail,
        "afiliado_pendente": afil_pend,
        "taxa_afiliado":    round(afil_ok / max(afil_ok + afil_fail, 1) * 100, 1),
        "top_ofertas":      [dict(r) for r in top],
        "ultimas_execucoes": [dict(r) for r in ultimas],
    }


# ── Execuções ─────────────────────────────────────────────────────────────────

def iniciar_execucao() -> int:
    now = datetime.now().isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO execucoes (iniciado_em) VALUES (?)", (now,)
        )
    return cur.lastrowid


def finalizar_execucao(exec_id: int, **kwargs) -> None:
    now = datetime.now().isoformat()
    campos = ", ".join(f"{k}=?" for k in kwargs)
    valores = list(kwargs.values()) + [now, exec_id]
    with _conn() as con:
        con.execute(
            f"UPDATE execucoes SET {campos}, concluido_em=? WHERE id=?",
            valores
        )


def registrar_erro(tipo: str, mensagem: str, produto_id: str = "") -> None:
    now = datetime.now().isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO erros_log (tipo, mensagem, produto_id, ocorrido_em) VALUES (?,?,?,?)",
            (tipo, mensagem, produto_id, now)
        )
