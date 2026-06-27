# -*- coding: utf-8 -*-
"""
Migração de dados JSON → SQLite.
Execute uma única vez: python migrate.py
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import core.database as db

_RAIZ = os.path.dirname(os.path.abspath(__file__))


def _ler_json(caminho: str) -> list:
    if not os.path.exists(caminho):
        return []
    with open(caminho, "r", encoding="utf-8") as f:
        return json.loads(f.read().strip() or "[]")


def migrar():
    print("Inicializando banco de dados...")
    db.inicializar()

    # Migra produtos.json
    produtos_path = os.path.join(_RAIZ, "produtos.json")
    produtos = _ler_json(produtos_path)
    print(f"Migrando {len(produtos)} produto(s) de produtos.json...")
    ok = 0
    for p in produtos:
        try:
            # Compatibilidade com campo "link" → deriva ID
            if "id" not in p or not p["id"]:
                url = p.get("link", "")
                p["id"] = url.split("?")[0].rstrip("/").split("/")[-1] or url[:60]
            if "adicionado_em" not in p or not p["adicionado_em"]:
                p["adicionado_em"] = "2025-01-01T00:00:00"
            if "titulo" not in p or not p["titulo"]:
                p["titulo"] = "Produto sem título"
            db.inserir_produto(p)
            ok += 1
        except Exception as e:
            print(f"  Erro ao migrar {p.get('id', '?')}: {e}")
    print(f"  {ok}/{len(produtos)} migrado(s).")

    # Migra historico.json como "enviados"
    hist_path = os.path.join(_RAIZ, "data", "historico.json")
    historico = _ler_json(hist_path)
    print(f"Marcando {len(historico)} item(ns) do histórico como enviados...")
    for item in historico:
        url = item if isinstance(item, str) else item.get("link", "")
        if url:
            produto_id = url.split("?")[0].rstrip("/").split("/")[-1] or url[:60]
            try:
                db.marcar_enviado(produto_id)
            except Exception:
                pass
    print("  Histórico migrado.")

    print("\nMigração concluída!")
    s = db.stats()
    print(f"  Total no banco: {s['total']} produto(s)")
    print(f"  Enviados:       {s['enviados']}")
    print(f"  Pendentes:      {s['pendentes']}")


if __name__ == "__main__":
    migrar()
