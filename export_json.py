# -*- coding: utf-8 -*-
"""
Exporta os últimos produtos enviados para docs/data/offers.json.
Executado automaticamente pelo GitHub Actions após cada run do bot.
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta

BASE     = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE, "data", "bot_ofertas.db")
OUT_PATH = os.path.join(BASE, "docs", "data", "offers.json")

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

SELECT_FIELDS = f"""
    titulo, preco, preco_original, desconto_pct,
    affiliate_link, foto, categoria, score, enviado_em{cupom_col}
"""

# ── Últimos 100 produtos enviados ─────────────────────────────────────────────
rows = con.execute(f"""
    SELECT {SELECT_FIELDS}
    FROM produtos
    WHERE status = 'enviado'
    ORDER BY enviado_em DESC
    LIMIT 100
""").fetchall()

products = []
for row in rows:
    p = dict(row)
    p["link"] = p.pop("affiliate_link") or ""
    if not p["link"]:
        continue
    products.append({k: v for k, v in p.items() if v is not None})

# ── Produto do Dia — maior score nas últimas 24h ──────────────────────────────
ontem = (datetime.now() - timedelta(hours=24)).isoformat()
dia_row = con.execute(f"""
    SELECT {SELECT_FIELDS}
    FROM produtos
    WHERE status = 'enviado' AND enviado_em >= ?
    ORDER BY score DESC, desconto_pct DESC
    LIMIT 1
""", (ontem,)).fetchone()

produto_do_dia = None
if dia_row:
    p = dict(dia_row)
    p["link"] = p.pop("affiliate_link") or ""
    if p["link"]:
        produto_do_dia = {k: v for k, v in p.items() if v is not None}

# ── Estatísticas por categoria ────────────────────────────────────────────────
stat_rows = con.execute("""
    SELECT categoria,
           COUNT(*) as total,
           ROUND(AVG(score), 0) as score_medio,
           ROUND(AVG(desconto_pct), 0) as desconto_medio,
           MAX(desconto_pct) as maior_desconto
    FROM produtos
    WHERE status = 'enviado'
    GROUP BY categoria
    ORDER BY total DESC
""").fetchall()

stats_cat = {}
for r in stat_rows:
    stats_cat[r["categoria"]] = {
        "total":          r["total"],
        "score_medio":    int(r["score_medio"] or 0),
        "desconto_medio": int(r["desconto_medio"] or 0),
        "maior_desconto": int(r["maior_desconto"] or 0),
    }

con.close()

# ── Gravar JSON ───────────────────────────────────────────────────────────────
data = {
    "products":      products,
    "produto_do_dia": produto_do_dia,
    "stats":         stats_cat,
    "updated_at":    datetime.now().isoformat(),
    "total":         len(products),
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Exportados {len(products)} produto(s) | "
      f"Produto do dia: {produto_do_dia['titulo'][:50] if produto_do_dia else 'nenhum'}")
