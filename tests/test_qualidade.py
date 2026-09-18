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


# ---------------------------------------------------------------------------
# Ordem das fontes da Amazon.
#
# Bug real (2026-09-17): as 15 URLs — inclusive a pagina de cupons — eram
# embaralhadas juntas, e o laco para assim que junta `limite*2` produtos, o que
# acontece depois de 2 ou 3 categorias. A pagina de cupons entrava numa loteria
# de 15 e perdia ~80% das vezes, entao o "Rastreador Amazon Cupons" passava
# rodadas inteiras sem olhar cupom nenhum. Logs das rodadas #277, #280 e #283:
# "0 com cupom de desconto" nas tres.
# ---------------------------------------------------------------------------

def test_fontes_curadas_da_amazon_vem_sempre_primeiro():
    import random  # noqa: PLC0415
    from integrations.amazon_scraper import _FONTES_CURADAS, _URLS_AMAZON  # noqa: PLC0415

    nomes_curados = [n for n, _ in _FONTES_CURADAS]
    assert "cupons" in nomes_curados, "a pagina de cupons saiu das fontes curadas"
    assert "ofertas_dia" in nomes_curados

    # Reproduz a ordem que o scraper monta, varias vezes: as curadas nunca
    # podem depender do sorteio.
    for _ in range(200):
        ordem = _FONTES_CURADAS + random.sample(_URLS_AMAZON, len(_URLS_AMAZON))
        assert [n for n, _ in ordem[:len(_FONTES_CURADAS)]] == nomes_curados, \
            "uma fonte curada caiu no sorteio — o bug de 2026-09-17 voltou"


def test_fonte_curada_nao_se_repete_na_lista_sorteada():
    """Duplicata faria a mesma pagina ser varrida duas vezes por rodada,
    gastando uma das poucas fontes que o corte por `limite*2` permite."""
    from integrations.amazon_scraper import _FONTES_CURADAS, _URLS_AMAZON  # noqa: PLC0415

    curadas = {n for n, _ in _FONTES_CURADAS}
    repetidas = curadas & {n for n, _ in _URLS_AMAZON}
    assert not repetidas, "fonte curada duplicada na lista sorteada: %s" % repetidas


def test_scraper_avisa_quando_categoria_vem_vazia():
    """Um zero tem de aparecer no log JA classificado. Foi a mudez do resumo
    ('0 com cupom', sem dizer por que) que escondeu o bug da ordem."""
    import pathlib as _p  # noqa: PLC0415

    raiz = _p.Path(__file__).resolve().parent.parent
    src = (raiz / "integrations" / "amazon_scraper.py").read_text(encoding="utf-8")
    assert "amazon[%s]: %d card(s) no DOM, %d produto(s) apos " in src, \
        "sumiu a contagem por fonte — '0 com cupom' volta a ser indiagnosticavel"
    assert "_DIAG_SCRIPT" in src, "sumiu o diagnostico de DOM do zero"


def test_zero_produto_distingue_bloqueio_de_seletor():
    """Zero produto tem tres causas e o log precisa dizer QUAL. Rodada #288
    (18/09, 03:04): 15 fontes com zero e a unica mensagem era 'seletor do DOM
    provavelmente mudou' — uma suposicao (Regra 2). Bloqueio anti-bot da
    Amazon tambem da zero, e ali reescrever seletor e trabalho jogado fora."""
    import pathlib as _p  # noqa: PLC0415

    raiz = _p.Path(__file__).resolve().parent.parent
    src = (raiz / "integrations" / "amazon_scraper.py").read_text(encoding="utf-8")

    assert "BLOQUEIO anti-bot" in src, "o log nao sabe mais nomear bloqueio"
    assert "nenhum passou" in src, "o log nao sabe mais nomear filtro de desconto"
    assert "ZERO card no DOM sem marca de bloqueio" in src, \
        "o log nao sabe mais nomear seletor podre"

    # O diagnostico so pode custar chamada quando ja deu zero.
    i_if = src.index("if not produtos:")
    i_eval = src.index("page.evaluate(_DIAG_SCRIPT)")
    assert i_eval > i_if, "diagnostico rodando fora do caminho de zero produto"

    # E nao pode derrubar a rodada se ele mesmo falhar.
    trecho = src[i_if:i_if + 600]
    assert "except Exception:" in trecho, \
        "diagnostico sem protecao — uma falha nele mataria a categoria inteira"


def test_diag_script_procura_marcas_reais_de_bloqueio():
    """As marcas sao o que a Amazon Brasil realmente escreve na pagina de
    bloqueio — em portugues e em ingles, porque o interstitial vem nos dois."""
    from integrations import amazon_scraper as a  # noqa: PLC0415

    for marca in ("Digite os caracteres", "Continuar comprando",
                  "automated access", "captchacharacters", "validateCaptcha"):
        assert marca in a._DIAG_SCRIPT, f"sumiu a marca de bloqueio {marca!r}"

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
