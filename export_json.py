# -*- coding: utf-8 -*-
"""
Exporta produtos enviados para docs/data/offers.json.
Executado pelo GitHub Actions após cada run — garante que o site
mostre exatamente os mesmos produtos postados no Telegram.
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import json
import os
import sqlite3
from datetime import datetime, timedelta

BASE     = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE, "data", "bot_ofertas.db")
OUT_PATH = os.path.join(BASE, "docs", "data", "offers.json")
MAX_PRODUTOS = 300   # máximo de produtos no site
JANELA_DIAS  = 14    # só mostra produtos dos últimos 14 dias

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

if not os.path.exists(DB_PATH):
    data = {"products": [], "produto_do_dia": None,
            "stats": {}, "updated_at": datetime.now().isoformat(), "total": 0}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("Banco não encontrado — JSON vazio gerado.")
    exit(0)

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
cols = {r[1] for r in con.execute("PRAGMA table_info(produtos)").fetchall()}
cupom_col = ", cupom" if "cupom" in cols else ", NULL as cupom"

CAMPOS = f"""
    id, titulo, preco, preco_original, desconto_pct,
    affiliate_link, foto, categoria, score, enviado_em{cupom_col}
"""

corte = (datetime.now() - timedelta(days=JANELA_DIAS)).isoformat()
tem_historico = "precos_historico" in {
    r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
}


def _classificar(score):
    """Espelha core.scorer.classificar_score (sem importar para manter export leve)."""
    s = score or 0
    if s >= 85: return "excelente"
    if s >= 65: return "boa"
    if s >= 45: return "media"
    return "ruim"


def _menor_preco(produto_id, dias=30):
    """Retorna (menor_preco, e_menor_periodo) dos últimos N dias, ou (None, False)."""
    if not tem_historico:
        return None, False
    corte_h = (datetime.now() - timedelta(days=dias)).isoformat()
    rows = con.execute(
        "SELECT preco FROM precos_historico WHERE produto_id=? AND visto_em>=? ORDER BY visto_em",
        (produto_id, corte_h),
    ).fetchall()
    if not rows:
        return None, False
    precos = [r[0] for r in rows]
    menor = round(min(precos), 2)
    e_menor = len(precos) >= 2 and precos[-1] <= menor + 0.01
    return menor, e_menor

# ── Todos os produtos enviados (últimos 14 dias, no máx 300) ─────────────────
rows = con.execute(f"""
    SELECT {CAMPOS}
    FROM produtos
    WHERE status = 'enviado'
      AND affiliate_link IS NOT NULL
      AND affiliate_link != ''
      AND enviado_em >= ?
    ORDER BY enviado_em DESC
    LIMIT ?
""", (corte, MAX_PRODUTOS)).fetchall()

products = []
for row in rows:
    p = dict(row)
    p["link"] = p.pop("affiliate_link", "") or ""
    if not p["link"]:
        continue
    pid = p.pop("id", None)
    p["classe"] = _classificar(p.get("score"))
    menor, e_menor = _menor_preco(pid)
    if menor is not None:
        p["menor_preco_30d"] = menor
        p["menor_periodo"]   = e_menor
    p["verificada"] = True  # passou por validação anti-fraude + link de afiliado OK
    products.append({k: v for k, v in p.items() if v is not None})

# ── Produto do Dia — maior score nas últimas 24h ──────────────────────────────
ontem = (datetime.now() - timedelta(hours=24)).isoformat()
dia_row = con.execute(f"""
    SELECT {CAMPOS}
    FROM produtos
    WHERE status = 'enviado'
      AND affiliate_link IS NOT NULL
      AND enviado_em >= ?
    ORDER BY score DESC, desconto_pct DESC
    LIMIT 1
""", (ontem,)).fetchone()

produto_do_dia = None
if dia_row:
    p = dict(dia_row)
    p["link"] = p.pop("affiliate_link", "") or ""
    pid = p.pop("id", None)
    if p["link"]:
        p["classe"] = _classificar(p.get("score"))
        menor, e_menor = _menor_preco(pid)
        if menor is not None:
            p["menor_preco_30d"] = menor
            p["menor_periodo"]   = e_menor
        p["verificada"] = True
        produto_do_dia = {k: v for k, v in p.items() if v is not None}

# ── Stats por categoria ───────────────────────────────────────────────────────
stat_rows = con.execute("""
    SELECT categoria,
           COUNT(*) as total,
           ROUND(AVG(score), 0) as score_medio,
           ROUND(AVG(desconto_pct), 0) as desconto_medio,
           MAX(desconto_pct) as maior_desconto
    FROM produtos
    WHERE status = 'enviado' AND enviado_em >= ?
    GROUP BY categoria
    ORDER BY total DESC
""", (corte,)).fetchall()

stats_cat = {r["categoria"]: {
    "total":          r["total"],
    "score_medio":    int(r["score_medio"] or 0),
    "desconto_medio": int(r["desconto_medio"] or 0),
    "maior_desconto": int(r["maior_desconto"] or 0),
} for r in stat_rows}

con.close()

data = {
    "products":       products,
    "produto_do_dia": produto_do_dia,
    "stats":          stats_cat,
    "updated_at":     datetime.now().isoformat(),
    "total":          len(products),
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

cat_counts = {}
for p in products:
    c = p.get("categoria","?")
    cat_counts[c] = cat_counts.get(c, 0) + 1

print(f"✅ {len(products)} produto(s) exportados | {len(cat_counts)} categoria(s)")
for cat, n in sorted(cat_counts.items(), key=lambda x: -x[1]):
    print(f"   {cat}: {n}")
if produto_do_dia:
    print(f"⭐ Produto do dia: {produto_do_dia['titulo'][:60]}")
