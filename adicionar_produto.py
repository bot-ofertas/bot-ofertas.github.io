# -*- coding: utf-8 -*-
"""
ADICIONAR PRODUTO À FILA
=========================
Assistente simples por linha de comando. Roda, faz 6 perguntas, salva tudo
certinho em produtos.json (em UTF-8, sem risco de "bagunçar" emoji ou acento
como acontecia colando no Bloco de Notas).

Como usar:
    python adicionar_produto.py

Antes de rodar, gere o link de afiliado no app ou site do Mercado Livre:
  1. Abra o produto que você quer divulgar.
  2. Toque em "Compartilhar" (se a Barra de Afiliados estiver ativada nas
     configurações do Portal do Afiliado) OU vá em Portal do Afiliado ->
     Gerador de link -> cole a URL do produto -> Gerar link.
  3. Copie o link gerado (ele já vem com o seu ID de afiliado).
  4. Cole esse link aqui quando o assistente pedir.
"""

import json
import os
from datetime import datetime

ARQUIVO = "produtos.json"


def carregar():
    if not os.path.exists(ARQUIVO):
        return []
    with open(ARQUIVO, "r", encoding="utf-8") as f:
        conteudo = f.read().strip()
        return json.loads(conteudo) if conteudo else []


def salvar(produtos):
    with open(ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(produtos, f, ensure_ascii=False, indent=2)


def pedir_preco(rotulo):
    valor = input(rotulo).strip().replace("R$", "").replace(",", ".").strip()
    if not valor:
        return None
    try:
        return float(valor)
    except ValueError:
        print("  (não entendi esse número, vou deixar em branco)")
        return None


def main():
    produtos = carregar()

    print("=== Adicionar novo produto à fila ===\n")
    link = input("1) Cole o LINK DE AFILIADO (já gerado no Portal do Afiliado do ML): ").strip()
    if not link:
        print("Link em branco, cancelando.")
        return

    titulo = input("2) Título do produto (como vai aparecer na mensagem): ").strip()
    preco = pedir_preco("3) Preço atual (ex: 149.90, ENTER se não souber): ")
    preco_original = pedir_preco("4) Preço ANTES do desconto (ENTER se não houver desconto): ")
    foto = input("5) Link de uma foto do produto (ENTER para enviar sem foto): ").strip()
    canal = input("6) Nome do canal (precisa existir em CANAIS no bot_ofertas.py, ENTER = 'geral'): ").strip()

    novo = {
        "id": f"p{len(produtos) + 1}_{int(datetime.now().timestamp())}",
        "titulo": titulo or "Produto sem título",
        "preco": preco,
        "preco_original": preco_original,
        "link": link,
        "foto": foto or None,
        "canal": canal or "geral",
        "status": "pendente",
        "adicionado_em": datetime.now().isoformat(),
    }

    produtos.append(novo)
    salvar(produtos)

    pendentes = sum(1 for p in produtos if p.get("status") != "enviado")
    print(f"\n✅ Produto adicionado! {pendentes} produto(s) aguardando envio na fila.")
    print("   Rode 'python bot_ofertas.py' quando quiser disparar a fila para o Telegram.")


if __name__ == "__main__":
    main()
