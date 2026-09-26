# -*- coding: utf-8 -*-
"""
Testes do terceiro marketplace (Magazine Luiza) e do funil de publicacao.

Por que estes dois num arquivo so: os dois nasceram da mesma pergunta do
Daniel — "por que nao saem as 3 ofertas, uma de cada marketplace?". A
resposta tinha duas metades: (a) o Magalu nunca foi implementado, entao a
terceira oferta nao existia; (b) nenhum rastreador contava onde as ofertas
se perdiam, entao nao dava para provar (a) sem ler o codigo.

Roda sem dependencia pesada (sem playwright, sem telegram) — o CI instala
so python-dotenv e requests. Por isso os testes de rastreador leem o
FONTE dos arquivos em vez de importa-los.

Rodar:
    python tests/test_magalu_funil.py
"""
import ast
import io
import json
import os
import re
import sys
from pathlib import Path

RAIZ = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(RAIZ))

from core.funil import ETAPAS, Funil  # noqa: E402
from integrations.magalu_scraper import (  # noqa: E402
    _preco, extrair_do_next_data, interpretar_diagnostico, normalizar,
)


def _com_vitrine(valor="magazineexemplo"):
    """Provider com MAGALU_VITRINE definida, recarregado do zero.

    O modulo le a variavel a cada chamada (nao cacheia em import), mas o
    reload mantem o teste honesto se isso mudar.
    """
    import importlib
    os.environ["MAGALU_VITRINE"] = valor
    import affiliates.magalu as mod
    importlib.reload(mod)
    return mod


# ── Afiliado Magalu ───────────────────────────────────────────────────────

def test_vitrine_entra_como_primeiro_segmento_do_caminho():
    mod = _com_vitrine()
    p = mod.MagaluAffiliateProvider()
    link = p.generate_affiliate_link(
        "https://www.magazineluiza.com.br/geladeira/p/123456700/ED/REFR/")
    from urllib.parse import urlsplit
    partes = [s for s in urlsplit(link).path.split("/") if s]
    assert urlsplit(link).hostname == "www.magazinevoce.com.br", link
    assert partes[0] == "magazineexemplo", link
    assert "geladeira" in partes, link


def test_fragmento_e_removido_e_query_preservada():
    """Regra 11: fragmento sobrevivente quebra afiliado E deduplicacao."""
    mod = _com_vitrine()
    p = mod.MagaluAffiliateProvider()
    link = p.generate_affiliate_link(
        "https://www.magazineluiza.com.br/fone/p/238899100/UD/F/?seller=x#track=y")
    from urllib.parse import parse_qs, urlsplit
    partes = urlsplit(link)
    assert partes.fragment == "", f"fragmento sobreviveu: {link}"
    assert parse_qs(partes.query) == {"seller": ["x"]}, link


def test_vitrine_de_outro_parceiro_e_substituida():
    """Republicar o link de outro parceiro manda a comissao para ele."""
    mod = _com_vitrine()
    p = mod.MagaluAffiliateProvider()
    link = p.generate_affiliate_link(
        "https://www.magazinevoce.com.br/magazineoutro/tv/p/999888100/ET/T/")
    from urllib.parse import urlsplit
    partes = [s for s in urlsplit(link).path.split("/") if s]
    assert partes[0] == "magazineexemplo", link
    assert "magazineoutro" not in link, link


def test_validacao_nao_e_por_substring():
    """Regra 3/4/11: `"vitrine" in link` aprova link que nao paga nada."""
    mod = _com_vitrine()
    p = mod.MagaluAffiliateProvider()
    falso = ("https://www.magazinevoce.com.br/outravitrine/"
             "magazineexemplo-tv/p/1234567/X/Y/")
    assert "magazineexemplo" in falso, "o teste perdeu o sentido"
    assert p.validate_affiliate_link(falso) is False, \
        "validacao aceitou a vitrine como substring do slug"


def test_vitrine_sozinha_nao_e_oferta():
    mod = _com_vitrine()
    p = mod.MagaluAffiliateProvider()
    assert p.validate_affiliate_link(
        "https://www.magazinevoce.com.br/magazineexemplo/") is False


def test_normaliza_as_formas_que_o_painel_devolve():
    for entrada in ("exemplo", "magazineexemplo", "  MagazineExemplo ",
                    "https://www.magazinevoce.com.br/magazineexemplo/"):
        mod = _com_vitrine(entrada)
        assert mod.vitrine() == "magazineexemplo", entrada


def test_vitrine_invalida_desativa_em_vez_de_gerar_link_quebrado():
    for ruim in ("nome com espaco", "acentuação", "", "   "):
        mod = _com_vitrine(ruim)
        assert mod.vitrine() == "", ruim
        assert mod.magalu_ativo() is False, ruim
        p = mod.MagaluAffiliateProvider()
        assert p.generate_affiliate_link(
            "https://www.magazineluiza.com.br/x/p/123456700/A/B/") is None, ruim


def test_sem_vitrine_o_provedor_se_declara_inativo():
    """Regra 7: sem link de afiliado valido, nao publica — e nao inventa."""
    import importlib
    os.environ.pop("MAGALU_VITRINE", None)
    import affiliates.magalu as mod
    importlib.reload(mod)
    p = mod.MagaluAffiliateProvider()
    assert p.health_check() is False
    assert p.generate_affiliate_link(
        "https://www.magazineluiza.com.br/x/p/123456700/A/B/") is None


# ── Scraper Magalu ────────────────────────────────────────────────────────

def _html_next(produtos):
    estado = {"props": {"pageProps": {"data": {"search": {"products": produtos}}}}}
    return ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(estado) + "</script></body></html>")


def test_extrai_do_next_data_com_preco_aninhado():
    """Bug real pego por este teste: `price: {bestPrice, listPrice}`.

    Procurar o preco cheio so no no de cima achava o preco de venda e
    perdia o cheio; sem preco cheio o desconto sai 0% e o filtro descarta
    uma oferta de 30% OFF como se fosse produto sem promocao.
    """
    html = _html_next([{
        "title": "Geladeira Brastemp 375L",
        "path": "/geladeira/p/123456700/ED/REFR/",
        "price": {"bestPrice": 2799.0, "listPrice": 3999.0},
        "image": {"url": "https://a-static.mlcdn.com.br/450x450/g.jpg"},
    }])
    produtos = normalizar(extrair_do_next_data(html), "teste", desconto_min=20)
    assert len(produtos) == 1, produtos
    assert produtos[0]["preco"] == 2799.0
    assert produtos[0]["preco_original"] == 3999.0
    assert produtos[0]["desconto_pct"] == 30.0


def test_banner_e_link_sem_codigo_nao_viram_produto():
    html = _html_next([
        {"title": "Banner de selecao", "url": "/selecao/ofertasdodia/"},
        {"title": "Departamento", "url": "/informatica/l/in/"},
    ])
    assert normalizar(extrair_do_next_data(html), "teste", 20) == []


def test_link_sai_sem_query_e_sem_fragmento():
    html = _html_next([{
        "title": "Fone JBL",
        "url": "https://www.magazineluiza.com.br/fone/p/238899100/UD/F/?utm=x#frag",
        "bestPrice": 199.9, "listPrice": 399.9,
    }])
    link = normalizar(extrair_do_next_data(html), "teste", 20)[0]["link"]
    assert "?" not in link and "#" not in link, link


def test_deduplica_pelo_codigo_oficial_nao_pelo_slug():
    """Regra 11: o mesmo produto aparece com slugs diferentes."""
    html = _html_next([
        {"title": "TV LG 50", "url": "/tv-lg-50-polegadas/p/999888100/ET/T/",
         "bestPrice": 1999.0, "listPrice": 2999.0},
        {"title": "TV LG 50 4K", "url": "/tv-lg-50-4k-smart/p/999888100/ET/T/",
         "bestPrice": 1999.0, "listPrice": 2999.0},
    ])
    produtos = normalizar(extrair_do_next_data(html), "teste", 20)
    assert len(produtos) == 1, "slug diferente virou produto novo"
    assert produtos[0]["codigo"] == "999888100"


def test_filtro_de_desconto_corta_produto_sem_promocao():
    html = _html_next([{"title": "Caneca", "url": "/caneca/p/111222300/UD/C/",
                        "price": 29.9, "listPrice": 29.9}])
    assert normalizar(extrair_do_next_data(html), "teste", 20) == []


def test_preco_aceita_numero_e_texto_br():
    assert _preco(2799.0) == 2799.0
    assert _preco("R$ 1.899,90") == 1899.9
    assert _preco("199,90") == 199.9
    assert _preco(0) is None
    assert _preco(None) is None
    assert _preco("sem preco") is None


def test_html_sem_next_data_nao_explode():
    assert extrair_do_next_data("<html><body>nada</body></html>") == []
    assert extrair_do_next_data(
        '<script id="__NEXT_DATA__">{quebrado</script>') == []


def test_diagnostico_nomeia_a_causa_em_vez_de_devolver_silencio():
    """Regra 2: `[]` nao e diagnostico. Bloqueio, layout e filtro pedem
    correcoes diferentes e nao podem chegar ao log como o mesmo vazio."""
    assert "BLOQUEIO" in interpretar_diagnostico({"captcha": True}, 0, 0)
    assert "LAYOUT MUDOU" in interpretar_diagnostico(
        {"temNextData": True, "tamanhoNextData": 9000,
         "linksProduto": 48, "cards": 24}, 0, 0)
    assert "FILTRO" in interpretar_diagnostico(
        {"temNextData": True, "linksProduto": 48}, 37, 0)
    assert interpretar_diagnostico({"temNextData": True}, 5, 2) == "ok"


# ── Funil ─────────────────────────────────────────────────────────────────

def test_funil_conserva_o_que_entrou():
    f = Funil("teste", meta=3)
    f.encontradas(20)
    f.marcar("duplicadas", 14)
    f.marcar("score_baixo", 3)
    f.marcar("sem_afiliado")
    f.marcar("publicadas", 2)
    assert f.conferir() == 0, "conservacao quebrada"
    assert f.publicadas == 2


def test_funil_denuncia_oferta_que_sumiu_sem_porta():
    """E exatamente o bug que o funil existe para tornar visivel: um
    `continue` sem contador faz a oferta sumir do balanco em silencio."""
    f = Funil("teste", meta=3)
    f.encontradas(20)
    f.marcar("publicadas", 1)
    assert f.conferir() == 19
    assert any("sem porta de saída" in linha for linha in f.linhas())


def test_funil_nomeia_a_maior_perda():
    f = Funil("mercadolivre", meta=3)
    f.encontradas(20)
    f.marcar("duplicadas", 17)
    f.marcar("publicadas", 1)
    motivo = f.motivo_do_teto()
    assert "1/3" in motivo and "duplicadas" in motivo.lower(), motivo


def test_funil_distingue_meta_atingida_de_falta_de_oferta():
    cheio = Funil("x", meta=1)
    cheio.encontradas(5)
    cheio.marcar("publicadas")
    cheio.marcar("nao_avaliadas", 4)
    assert "meta de 1 atingida" in cheio.motivo_do_teto()

    vazio = Funil("x", meta=3)
    vazio.encontradas(0)
    assert "scraper não devolveu" in vazio.motivo_do_teto()


def test_funil_nao_levanta_com_etapa_desconhecida():
    """Um contador errado nunca pode ser o que derruba uma publicacao."""
    f = Funil("x", meta=1)
    f.encontradas(1)
    f.marcar("etapa_que_nao_existe")
    assert f.etapas["erros"] == 1


def test_funil_grava_e_le_de_volta(tmp=None):
    import importlib
    destino = RAIZ / "data" / "funil_teste_tmp.jsonl"
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino.unlink()
    os.environ["FUNIL_ARQUIVO"] = str(destino)
    import core.funil as mod
    importlib.reload(mod)
    try:
        f = mod.Funil("magalu", meta=1)
        f.encontradas(4)
        f.marcar("publicadas")
        f.marcar("duplicadas", 3)
        f.fechar()
        lidos = mod.ultimo_por_fonte()
        assert "magalu" in lidos, lidos
        assert lidos["magalu"]["publicadas"] == 1
        assert lidos["magalu"]["nao_contabilizadas"] == 0
    finally:
        if destino.exists():
            destino.unlink()
        os.environ.pop("FUNIL_ARQUIVO", None)
        importlib.reload(mod)


# ── Fiacao nos rastreadores (por FONTE, sem importar) ─────────────────────

def _fonte(arquivo):
    return io.open(RAIZ / arquivo, encoding="utf-8").read()


def _sem_comentarios(src):
    """Tira linhas de comentario antes de procurar codigo.

    Ja mordeu tres vezes neste projeto: o teste casava com o COMENTARIO que
    explicava por que nao fazer aquilo, e passava sem o codigo existir.
    """
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


def test_os_tres_rastreadores_alimentam_o_funil():
    for arquivo in ("rastreador.py", "rastreador_amazon.py", "rastreador_magalu.py"):
        src = _sem_comentarios(_fonte(arquivo))
        assert "from core.funil import Funil" in src, f"{arquivo} sem funil"
        assert "funil.encontradas(" in src, f"{arquivo} nao conta o que achou"
        assert 'funil.marcar("publicadas")' in src, f"{arquivo} nao conta publicadas"
        assert "funil.fechar(" in src, f"{arquivo} nunca fecha o funil"


def test_nenhum_descarte_do_magalu_fica_sem_contador():
    """Cada `continue` do laco de itens tem que marcar uma porta.

    AST, nao regex: regex casa com comentario, e este projeto ja teve dois
    testes vazios exatamente por isso.
    """
    arvore = ast.parse(_fonte("rastreador_magalu.py"))
    laco = None
    for no in ast.walk(arvore):
        if isinstance(no, ast.For) and isinstance(no.target, ast.Tuple):
            alvos = [e.id for e in no.target.elts if isinstance(e, ast.Name)]
            if "item" in alvos:
                laco = no
                break
    assert laco is not None, "laco de itens do Magalu nao encontrado"

    def marca_funil(corpo):
        for no in corpo:
            for filho in ast.walk(no):
                if (isinstance(filho, ast.Call)
                        and isinstance(filho.func, ast.Attribute)
                        and filho.func.attr == "marcar"
                        and isinstance(filho.func.value, ast.Name)
                        and filho.func.value.id == "funil"):
                    return True
        return False

    # Cada bloco que termina em `continue` precisa marcar antes de sair.
    nus = 0
    for no in ast.walk(laco):
        corpo = getattr(no, "body", None)
        if not isinstance(corpo, list):
            continue
        if not any(isinstance(x, ast.Continue) for x in corpo):
            continue
        if not marca_funil(corpo):
            nus += 1
    assert nus == 0, f"{nus} descarte(s) do Magalu saem sem contar no funil"


def test_magalu_valida_o_link_antes_de_publicar():
    """Regra 7: sem link de afiliado valido nao publica — e a checagem
    tem que ser a do provedor, nao um `in` em cima da string."""
    src = _sem_comentarios(_fonte("rastreador_magalu.py"))
    assert "validate_affiliate_link(link_afiliado)" in src
    i = src.index("validate_affiliate_link")
    j = src.index("await publicar(")
    assert i < j, "o link e publicado antes de ser validado"


def test_magalu_deduplica_no_banco_e_no_registro_compartilhado():
    """Regra 16: os tres publicadores tem bancos separados."""
    src = _sem_comentarios(_fonte("rastreador_magalu.py"))
    assert "produto_id_existe" in src and "ja_publicado" in src
    i = src.index("publicados_site")
    assert "except Exception" in src[max(0, i - 400):i + 400], \
        "usa o registro compartilhado sem protecao"


def test_magalu_registra_falha_e_manda_para_quarentena():
    """Regra 12: produto que falha nunca volta indefinidamente."""
    src = _sem_comentarios(_fonte("rastreador_magalu.py"))
    for esperado in ("registrar_falha_publicacao", "em_quarentena",
                     "limpar_falha_publicacao"):
        assert esperado in src, f"Magalu sem {esperado}"


def test_magalu_respeita_pausa_papel_e_dns():
    src = _sem_comentarios(_fonte("rastreador_magalu.py"))
    for esperado in ("pausa.pausado()", "_papel.bloqueado()", "dns_ok("):
        assert esperado in src, f"Magalu ignora {esperado}"


def test_whatsapp_do_magalu_vai_pela_fila_nao_junto_do_telegram():
    """Regra 5: intervalo randomico de 30-45 min; Regra 6: Telegram nunca
    depende do WhatsApp."""
    src = _sem_comentarios(_fonte("rastreador_magalu.py"))
    assert "enfileirar_whatsapp" in src
    assert "whatsapp_desktop" not in src, "Magalu envia WhatsApp direto"


def test_startup_so_sobe_o_magalu_com_vitrine_configurada():
    """Sem o guard o processo sai em ~1s e queima as 3 tentativas do
    supervisor a cada rodada (mesmo padrao do .env invalido, Regra 15)."""
    src = _sem_comentarios(_fonte("startup.py"))
    assert "_iniciar_magalu" in src
    bloco = src[src.index("def _iniciar_magalu"):]
    bloco = bloco[:bloco.index("def _iniciar_ferramentas")]
    assert "magalu_ativo()" in bloco and "return None" in bloco, \
        "startup sobe o Magalu sem checar a vitrine"
    assert "rastreador_magalu.py" in src


def test_monitorar_aceita_tupla_de_tamanhos_diferentes():
    """A entrada do Magalu mudou a aridade de 4 para 5; desempacotamento
    fixo viraria ValueError e mataria o supervisor inteiro."""
    src = _sem_comentarios(_fonte("startup.py"))
    assert "proc_ml, proc_az, proc_ferr, proc_wa = procs" not in src, \
        "desempacotamento fixo de volta em monitorar()"


def test_registro_do_site_reconhece_id_do_magalu():
    from core.publicados_site import _ID_NO_NOME
    assert _ID_NO_NOME.search("geladeira-MLU123456700.html")
    # E continua recusando cauda de slug (falso positivo faria o bot PULAR
    # oferta boa, em silencio).
    assert not _ID_NO_NOME.search("slug-que-termina-em-22099816.html")


def test_health_expoe_o_funil():
    """Regra 11: toda integracao reporta saude propria no /health."""
    src = _sem_comentarios(_fonte("core/healthcheck.py"))
    assert '"funil": _status_funil()' in src
    assert "/funil" in src


def test_env_example_documenta_a_vitrine():
    texto = _fonte(".env.example")
    assert "MAGALU_VITRINE" in texto
    assert "MAX_POR_RODADA_MAGALU" in texto
    # E o valor real do Daniel nao pode ter entrado junto (Regra: sem
    # segredo em arquivo versionado).
    achado = re.search(r"^MAGALU_VITRINE=(.*)$", texto, re.M)
    assert achado and achado.group(1).strip() == "", \
        "MAGALU_VITRINE do .env.example veio preenchida"


def test_workflow_roda_o_magalu_sem_vazar_a_vitrine():
    texto = _fonte(".github/workflows/bot.yml")
    assert "rastreador_magalu.py" in texto
    assert "vars.MAGALU_VITRINE" in texto, "vitrine fora das variaveis do repo"
    assert not re.search(r"MAGALU_VITRINE:\s*magazine\w", texto), \
        "vitrine literal escrita no workflow"


def test_esta_suite_roda_sem_as_dependencias_pesadas():
    """CI instala so python-dotenv e requests. Importar um rastreador aqui
    puxaria telegram/playwright e quebraria o build — ja aconteceu."""
    arvore = ast.parse(io.open(__file__, encoding="utf-8").read())
    proibidos = {"telegram", "playwright", "psutil", "yaml", "PIL"}
    for no in ast.walk(arvore):
        nomes = []
        if isinstance(no, ast.Import):
            nomes = [a.name.split(".")[0] for a in no.names]
        elif isinstance(no, ast.ImportFrom) and no.module:
            nomes = [no.module.split(".")[0]]
        for nome in nomes:
            assert nome not in proibidos, f"import pesado na suite: {nome}"
        # E nao pode importar os rastreadores (eles puxam telegram no topo).
        for nome in nomes:
            assert not nome.startswith("rastreador"), \
                f"suite importa {nome} em vez de ler o fonte"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
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
