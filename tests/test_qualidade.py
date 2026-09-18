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
    codigo = _diag_sem_comentarios()

    for marca in ("Digite os caracteres", "Continuar comprando",
                  "automated access", "captchacharacters", "validateCaptcha"):
        assert marca in codigo, f"sumiu a marca de bloqueio {marca!r}"

def _diag_sem_comentarios():
    """O _DIAG_SCRIPT sem as linhas de comentario do JS.

    Procurar uma marca no texto inteiro do script casa com o COMENTARIO que
    explica a marca — o teste passa mesmo com a checagem arrancada. Foi o que
    aconteceu quando exercitei a regressao: tirei `includes('Algo deu errado')`
    do codigo e o teste seguiu verde porque a frase continuava no comentario
    logo acima.
    """
    import re as _re  # noqa: PLC0415
    from integrations import amazon_scraper as a  # noqa: PLC0415

    linhas = [l for l in a._DIAG_SCRIPT.split("\n")
              if not l.strip().startswith("//")]
    return _re.sub(r"/\*.*?\*/", "", "\n".join(linhas), flags=_re.S)


def test_extrair_da_pagina_aplica_o_filtro_e_deixa_cupom_passar():
    """Exercita a funcao de verdade, com uma pagina de mentira: e ela que as
    DUAS tentativas usam, entao um defeito aqui vale em dobro."""
    import asyncio  # noqa: PLC0415
    from integrations import amazon_scraper as a  # noqa: PLC0415

    cards = [
        # 40% OFF -> passa
        {"titulo": "Furadeira de Impacto 650W", "link": "https://www.amazon.com.br/dp/B0ABCDEFGH",
         "precoTexto": "R$ 120,00", "origTexto": "R$ 200,00", "descTexto": "40% OFF",
         "foto": "https://img/x.jpg", "cupomTexto": ""},
        # 5% OFF sem cupom -> cortado
        {"titulo": "Cabo USB-C Reforcado 2m", "link": "https://www.amazon.com.br/dp/B0IJKLMNOP",
         "precoTexto": "R$ 95,00", "origTexto": "R$ 100,00", "descTexto": "5% OFF",
         "foto": "https://img/y.jpg", "cupomTexto": ""},
        # 5% OFF MAS com cupom -> passa mesmo abaixo do minimo
        {"titulo": "Escova Secadora Rotativa", "link": "https://www.amazon.com.br/dp/B0QRSTUVWX",
         "precoTexto": "R$ 95,00", "origTexto": "R$ 100,00", "descTexto": "5% OFF",
         "foto": "https://img/z.jpg", "cupomTexto": "Cupom de R$ 30"},
    ]

    class _PaginaFalsa:
        async def evaluate(self, _script):
            return cards

    raw, produtos = asyncio.run(
        a._extrair_da_pagina(_PaginaFalsa(), "ferramentas", 20))

    assert len(raw) == 3, "a funcao nao pode mexer nos cards crus"
    titulos = [x["titulo"] for x in produtos]
    assert any("Furadeira" in t for t in titulos), "cortou uma oferta de 40%"
    assert not any("Cabo USB-C" in t for t in titulos), "5% sem cupom passou pelo filtro"
    assert any("Escova" in t for t in titulos), \
        "cupom abaixo do desconto minimo foi cortado — e ele que o rastreador procura"


def test_toda_url_da_amazon_sai_com_a_tag_de_afiliado_como_query():
    """Regra 4: `tag` tem de chegar como parametro de query DE VERDADE.
    Checagem por substring aceitaria a tag presa dentro de um #fragment."""
    from urllib.parse import urlsplit, parse_qs  # noqa: PLC0415
    from integrations import amazon_scraper as a  # noqa: PLC0415

    if not a._AFFILIATE_TAG:
        a._AFFILIATE_TAG = "silver1230c-20"

    sujas = [
        "https://www.amazon.com.br/dp/B0ABCDEFGH",
        "https://www.amazon.com.br/dp/B0ABCDEFGH?psc=1&ref=lixo",
        "https://www.amazon.com.br/dp/B0ABCDEFGH#tracking=abc",
        "https://www.amazon.com.br/dp/B0ABCDEFGH?psc=1#tracking=abc",
    ]
    for suja in sujas:
        link = a._link_afiliado(suja)
        partes = urlsplit(link)
        q = parse_qs(partes.query)
        assert q.get("tag") == [a._AFFILIATE_TAG], \
            f"tag nao chegou como query real em {suja!r} -> {link!r}"
        assert partes.fragment == "", \
            f"fragmento sobreviveu em {link!r} (Regra 11)"


def test_erro_de_servidor_da_amazon_nao_e_confundido_com_seletor():
    """Rodada #291 (18/09, 21:31): treze fontes com titulo 'Algo deu errado' e
    corpo vazio, enquanto brinquedos trouxe 24 cards na MESMA rodada — os
    seletores estavam certos o tempo todo. O ramo de erro/throttle tem de vir
    antes do de seletor, senao o diagnostico acusa o inocente."""
    import pathlib as _p  # noqa: PLC0415

    codigo = _diag_sem_comentarios()

    for marca in ("includes('Algo deu errado')", "includes('Something went wrong')",
                  "erro_servidor"):
        assert marca in codigo, f"sumiu a checagem de erro de servidor {marca!r}"

    raiz = _p.Path(__file__).resolve().parent.parent
    src = (raiz / "integrations" / "amazon_scraper.py").read_text(encoding="utf-8")
    i_erro = src.index('elif d.get("erro_servidor")')
    i_seletor = src.index("ZERO card no DOM sem marca de bloqueio")
    assert i_erro < i_seletor, \
        "o ramo de seletor esta capturando o caso de erro/throttle"


def test_retentativa_da_amazon_e_uma_so():
    """Insistir ate conseguir e o caminho de virar bloqueio de verdade. A
    segunda tentativa existe, roda so no caso de erro/throttle, e nao pode
    virar laco."""
    import ast as _ast  # noqa: PLC0415
    import pathlib as _p  # noqa: PLC0415

    raiz = _p.Path(__file__).resolve().parent.parent
    src = (raiz / "integrations" / "amazon_scraper.py").read_text(encoding="utf-8")

    i_erro = src.index('elif d.get("erro_servidor")')
    i_fim = src.index("elif len(raw) > 0:", i_erro)
    ramo = src[i_erro:i_fim]

    assert "page.reload(" in ramo, "sumiu a segunda tentativa"
    assert ramo.count("page.reload(") == 1, "mais de um reload no mesmo ramo"
    for laco in ("while ", "for "):
        assert laco not in ramo, f"a retentativa virou laco ({laco.strip()})"
    assert "_extrair_da_pagina" in ramo, \
        "a retentativa nao usa o mesmo extrator — os dois filtros vao divergir"

    # E o reload tem de estar protegido: um erro nele nao pode matar a rodada.
    assert "except Exception as e2:" in ramo, "retentativa sem protecao"

    # Os DOIS desfechos da retentativa tem de aparecer no log. Uma retentativa
    # que volta vazia em silencio faz "throttle passageiro" parecer igual a
    # "esta fonte recusa sempre" — foi o caso de /coupons e /deals na rodada
    # #292, e e a mesma mudez que escondeu o bug da ordem por rodadas seguidas.
    assert "recuperado na 2a tentativa" in ramo, "sumiu o log do sucesso"
    assert "2a tentativa tambem veio" in ramo, \
        "retentativa que volta vazia voltou a ser muda"

    # A pausa existe e e uma constante, nao um numero solto no meio do laco.
    arvore = _ast.parse(src)
    nomes = {n.targets[0].id for n in arvore.body
             if isinstance(n, _ast.Assign) and isinstance(n.targets[0], _ast.Name)}
    assert "_PAUSA_RETENTATIVA_MS" in nomes, "a pausa da retentativa sumiu"


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
