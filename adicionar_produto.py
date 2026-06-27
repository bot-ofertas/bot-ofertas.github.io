# -*- coding: utf-8 -*-
"""
ADICIONAR PRODUTO À FILA
=========================
Cole o link de afiliado — o bot extrai título, preço e foto automaticamente.
Você só confirma ou corrige o que estiver errado.

Como usar:
    python adicionar_produto.py
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from core.scorer import calcular_score, COMISSOES_ML
from core.deduplicator import e_duplicata
from integrations.ml_extrator import extrair

ARQUIVO = "produtos.json"


def _carregar() -> list[dict]:
    if not os.path.exists(ARQUIVO):
        return []
    with open(ARQUIVO, "r", encoding="utf-8") as f:
        return json.loads(f.read().strip() or "[]")


def _salvar(produtos: list[dict]) -> None:
    with open(ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(produtos, f, ensure_ascii=False, indent=2)


def _pedir_preco(rotulo: str, sugestao: float | None = None) -> float | None:
    hint = f" [ENTER = {sugestao:.2f}]" if sugestao else " [ENTER para pular]"
    valor = input(f"{rotulo}{hint}: ").strip().replace("R$", "").replace(",", ".").strip()
    if not valor:
        return sugestao
    try:
        return float(valor)
    except ValueError:
        print("  (não entendi esse número)")
        return sugestao


def _confirmar(rotulo: str, valor: str) -> str:
    entrada = input(f"{rotulo} [ENTER = '{valor}']: ").strip()
    return entrada if entrada else valor


def main() -> None:
    produtos = _carregar()
    print("=== Adicionar produto à fila ===\n")

    link = input("Cole o LINK DE AFILIADO (meli.la/... ou URL completa): ").strip()
    if not link:
        print("Link em branco, cancelando.")
        return

    # Extração automática
    print("  🔍 Buscando dados do produto...")
    dados = None
    try:
        dados = extrair(link)
    except RuntimeError as e:
        print(f"  ⚠️  Não consegui acessar o link: {e}")

    if dados:
        print(f"  ✅ Produto encontrado!\n")
        link_final = dados.get("link", link)
    else:
        print("  ⚠️  Não consegui extrair dados automaticamente. Preencha manualmente.\n")
        link_final = link
        dados = {}

    # Confirmação / correção dos dados extraídos
    titulo        = _confirmar("Título", dados.get("titulo") or "Produto sem título")
    preco         = _pedir_preco("Preço atual (R$)",        dados.get("preco"))
    preco_original= _pedir_preco("Preço antes do desconto", dados.get("preco_original"))
    foto_sugerida = dados.get("foto") or ""
    foto_entrada  = input(f"Foto (URL) [ENTER = {'extraída automaticamente' if foto_sugerida else 'sem foto'}]: ").strip()
    foto          = foto_entrada or foto_sugerida or None

    cats = ", ".join(COMISSOES_ML.keys())
    categoria = input(f"Categoria ({cats}) [ENTER = geral]: ").strip().lower() or "geral"
    canal     = input("Canal (ENTER = 'geral'): ").strip() or "geral"

    novo = {
        "id":              f"p{len(produtos) + 1}_{int(datetime.now().timestamp())}",
        "titulo":          titulo,
        "preco":           preco,
        "preco_original":  preco_original,
        "link":            link_final,
        "foto":            foto,
        "categoria":       categoria,
        "canal":           canal,
        "status":          "pendente",
        "adicionado_em":   datetime.now().isoformat(),
    }
    novo["score"] = calcular_score(novo)

    if e_duplicata(novo):
        print(f"\n⚠️  Produto parece duplicado (mesmo link ou título similar já enviado).")
        if input("Adicionar mesmo assim? (s/N): ").strip().lower() != "s":
            print("Cancelado.")
            return

    produtos.append(novo)
    _salvar(produtos)

    score     = novo["score"]
    qualidade = "🟢 ótima" if score >= 75 else ("🟡 boa" if score >= 50 else "🔴 baixa")
    pendentes = sum(1 for p in produtos if p.get("status") not in ("enviado", "duplicata"))

    print(f"\n✅ Produto adicionado!")
    print(f"   Score: {score}/100 ({qualidade})")
    print(f"   {pendentes} produto(s) na fila. Rode 'python bot_ofertas.py' para enviar.")


if __name__ == "__main__":
    main()
