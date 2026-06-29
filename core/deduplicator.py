# -*- coding: utf-8 -*-
"""
Detecção de duplicatas: compara hash de URL e similaridade de título (Jaccard).
Mantém janela de 30 dias em data/historico.json.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta

_HISTORICO = os.path.join(os.path.dirname(__file__), "..", "data", "historico.json")
_JANELA_DIAS = 2   # limpa automaticamente a cada 2 dias
_LIMIAR = 0.80


def _hash_link(link: str) -> str:
    url_base = link.strip().split("?")[0]
    return hashlib.md5(url_base.encode()).hexdigest()


def _jaccard(a: str, b: str) -> float:
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _carregar() -> list[dict]:
    if not os.path.exists(_HISTORICO):
        return []
    with open(_HISTORICO, "r", encoding="utf-8") as f:
        return json.loads(f.read().strip() or "[]")


def _salvar(historico: list[dict]) -> None:
    os.makedirs(os.path.dirname(_HISTORICO), exist_ok=True)
    with open(_HISTORICO, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=2)


def e_duplicata(produto: dict) -> bool:
    corte = datetime.now() - timedelta(days=_JANELA_DIAS)
    historico = [
        h for h in _carregar()
        if datetime.fromisoformat(h["enviado_em"]) > corte
    ]
    hash_url = _hash_link(produto.get("link", ""))
    titulo = produto.get("titulo", "")
    for entrada in historico:
        if entrada["hash"] == hash_url:
            return True
        if _jaccard(titulo, entrada.get("titulo", "")) >= _LIMIAR:
            return True
    return False


def registrar_envio(produto: dict) -> None:
    historico = _carregar()
    corte = datetime.now() - timedelta(days=_JANELA_DIAS)
    historico = [h for h in historico if datetime.fromisoformat(h["enviado_em"]) > corte]
    historico.append({
        "hash": _hash_link(produto.get("link", "")),
        "titulo": produto.get("titulo", ""),
        "enviado_em": datetime.now().isoformat(),
    })
    _salvar(historico)
