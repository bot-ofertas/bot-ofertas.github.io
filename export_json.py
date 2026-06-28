# -*- coding: utf-8 -*-
"""
Exporta os últimos produtos enviados para docs/data/offers.json.
Executado automaticamente pelo GitHub Actions após cada run do bot.
"""
import json
import os
import sqlite3
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "bot_ofertas.db")
OUT_PATH = os.path.join(BASE, "docs", "data", "offers.json")

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

if not os.path.exists(DB_PATH):
    data = {"products": [], "updated_at": datetime.now().isoformat(), "total": 0}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("Banco não encontrado — JSON vazio gerado.")
    exit(0)

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

# Verifica se coluna cupom existe
cols = {r[1] for r in con.execute("PRAGMA table_info(produtos)").fetchall()}
cupom_col = ", cupom" if "cupom" in cols else ", NULL as cupom"

rows = con.execute(f"""
    SELECT titulo, preco, preco_original, desconto_pct,
           affiliate_link, foto, categoria, score, enviado_em{cupom_col}
    FROM produtos
    WHERE status = 'enviado'
    ORDER BY enviado_em DESC
    LIMIT 40
""").fetchall()

products = []
for row in rows:
    p = dict(row)
    p["link"] = p.pop("affiliate_link") or ""
    if not p["link"]:
        continue
    # Remove campos None para JSON limpo
    products.append({k: v for k, v in p.items() if v is not None})

con.close()

data = {
    "products": products,
    "updated_at": datetime.now().isoformat(),
    "total": len(products),
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Exportados {len(products)} produto(s) para {OUT_PATH}")
