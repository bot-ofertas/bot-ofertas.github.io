# -*- coding: utf-8 -*-
"""
Testes do núcleo de qualidade: score, classificação, anti-fraude e
preservação do parâmetro de afiliado nos links.

Rodar:
    python -m pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.scorer import calcular_score, classificar_score, selo_classificacao
from core.validador import validar
from affiliates.mercadolivre import MLAffiliateProvider


# ── Score ─────────────────────────────────────────────────────────────────────

def test_score_oferta_boa_e_alto():
    p = {
        "preco": 1399.0, "preco_original": 2199.0,  # ~36% OFF
        "categoria": "celulares", "foto": "http://img/x.webp",
        "titulo": "Smartphone Samsung Galaxy A36 5G 128GB Câmera 50MP",
        "avaliacoes": 4.6, "quantidade_vendida": 1200,
    }
    score = calcular_score(p)
    assert 70 <= score <= 100


def test_score_produto_fraco_e_baixo():
    p = {"preco": 19.9, "preco_original": 22.0, "categoria": "geral", "titulo": "x"}
    assert calcular_score(p) < 45


def test_score_limita_entre_0_e_100():
    p = {
        "preco": 10.0, "preco_original": 5000.0, "categoria": "beleza",
        "foto": "y", "titulo": "T" * 80, "avaliacoes": 5.0, "quantidade_vendida": 99999,
    }
    s = calcular_score(p)
    assert 0 <= s <= 100


# ── Classificação ─────────────────────────────────────────────────────────────

def test_classificar_thresholds():
    assert classificar_score(90) == "excelente"
    assert classificar_score(85) == "excelente"
    assert classificar_score(70) == "boa"
    assert classificar_score(50) == "media"
    assert classificar_score(10) == "ruim"


def test_selo_retorna_emoji_e_rotulo():
    emoji, rotulo = selo_classificacao(90)
    assert emoji and isinstance(rotulo, str)


# ── Anti-fraude (validador) ───────────────────────────────────────────────────

def test_rejeita_desconto_irreal():
    p = {"preco": 100.0, "preco_original": 1000.0, "desconto_pct": 90.0}  # 90% suspeito
    aprovado, motivo = validar(p, reputacao={})
    assert aprovado is False
    assert "inflado" in motivo or "irreal" in motivo


def test_aprova_oferta_legitima():
    p = {"preco": 1399.0, "preco_original": 2199.0, "desconto_pct": 36.0}
    aprovado, _ = validar(p, reputacao={})
    assert aprovado is True


# ── Preservação do parâmetro de afiliado ──────────────────────────────────────

def test_link_direto_preserva_matt_tool():
    prov = MLAffiliateProvider()
    url = "https://www.mercadolivre.com.br/produto/p/MLB123?ref=lixo&utm=spam"
    link = prov._link_direto_com_afiliado(url)
    assert "matt_tool=" in link
    assert MLAffiliateProvider._TOOL_ID in link
    # query antiga descartada antes de aplicar o afiliado
    assert "ref=lixo" not in link
    assert "utm=spam" not in link


def test_validate_affiliate_link():
    prov = MLAffiliateProvider()
    assert prov.validate_affiliate_link("https://meli.la/abc123") is True
    assert prov.validate_affiliate_link(
        f"https://x/p/MLB1?matt_tool={MLAffiliateProvider._TOOL_ID}") is True
    assert prov.validate_affiliate_link("https://x/p/MLB1") is False
    assert prov.validate_affiliate_link("") is False


if __name__ == "__main__":
    # Permite rodar sem pytest: python tests/test_qualidade.py
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    falhas = 0
    for fn in fns:
        try:
            fn()
            print(f"  [OK]   {fn.__name__}")
        except Exception:
            falhas += 1
            print(f"  [FAIL] {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - falhas}/{len(fns)} testes passaram.")
    sys.exit(1 if falhas else 0)
