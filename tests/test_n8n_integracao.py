# -*- coding: utf-8 -*-
"""
Testes da integração n8n, da quarentena de publicação, do tracking por canal
e da normalização de foto.

Cobrem exatamente os pontos que já quebraram em produção:
  - link de afiliado sobrevivendo à troca de origem por canal (Regras 3/4/11);
  - URL de foto com `#fragment` e miniatura virando alta resolução;
  - produto que falha ao publicar saindo de rotação em vez de repetir para
    sempre (caso MLB68674214);
  - assinatura HMAC dos eventos enviados ao n8n;
  - workflows do n8n bem formados (nós, conexões e credenciais).

Rodar:
    python tests/test_n8n_integracao.py     # sem dependências extras
    python -m pytest tests/ -v
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Tracking por canal ───────────────────────────────────────────────────────

def test_troca_origem_preserva_matt_tool():
    """A troca de canal NUNCA pode comer a comissão (Regra 3)."""
    from urllib.parse import parse_qs, urlsplit
    from core.tracking import afiliado_intacto, marcar_origem

    link = ("https://www.mercadolivre.com.br/produto/p/MLB123"
            "?matt_tool=47114387&matt_source=bot_telegram")
    novo = marcar_origem(link, "whatsapp")
    q = parse_qs(urlsplit(novo).query)
    assert q["matt_tool"] == ["47114387"]          # comissão preservada
    assert q["matt_source"] == ["bot_whatsapp"]    # origem trocada
    assert afiliado_intacto(novo, matt_tool="47114387")


def test_troca_origem_em_link_sem_matt_source():
    """O `str.replace` antigo virava no-op silencioso neste caso."""
    from urllib.parse import parse_qs, urlsplit
    from core.tracking import marcar_origem

    link = "https://www.mercadolivre.com.br/p/MLB999?matt_tool=47114387"
    novo = marcar_origem(link, "instagram")
    q = parse_qs(urlsplit(novo).query)
    assert q["matt_source"] == ["instagram"]
    assert q["matt_tool"] == ["47114387"]


def test_troca_origem_descarta_fragmento_de_tracking():
    """`#polycard_client=...` antes da query esconde o afiliado (Regra 11)."""
    from urllib.parse import parse_qs, urlsplit
    from core.tracking import marcar_origem

    link = ("https://www.mercadolivre.com.br/p/MLB1?matt_tool=47114387"
            "#polycard_client=search-nordic")
    novo = marcar_origem(link, "whatsapp")
    assert "#" not in novo
    assert parse_qs(urlsplit(novo).query)["matt_tool"] == ["47114387"]


def test_amazon_usa_ascsubtag_e_preserva_tag():
    from urllib.parse import parse_qs, urlsplit
    from core.tracking import afiliado_intacto, marcar_origem

    link = "https://www.amazon.com.br/dp/B0ABC?tag=silver1230c-20"
    novo = marcar_origem(link, "whatsapp")
    q = parse_qs(urlsplit(novo).query)
    assert q["tag"] == ["silver1230c-20"]
    assert q["ascsubtag"] == ["bot_whatsapp"]
    assert afiliado_intacto(novo, amazon_tag="silver1230c-20")


def test_marcar_link_no_texto_da_mensagem_whatsapp():
    """O caminho real: a troca acontece no texto inteiro da mensagem."""
    from urllib.parse import parse_qs, urlsplit
    from integrations.whatsapp_sender import marcar_link_para_whatsapp

    texto = ("🔥 Oferta boa\n"
             "👉 https://www.mercadolivre.com.br/p/MLB1?matt_tool=47114387&matt_source=bot_telegram\n"
             "Aproveite!")
    novo = marcar_link_para_whatsapp(texto)
    url = [p for p in novo.split() if p.startswith("http")][0]
    q = parse_qs(urlsplit(url).query)
    assert q["matt_source"] == ["bot_whatsapp"]
    assert q["matt_tool"] == ["47114387"]


# ── Foto ─────────────────────────────────────────────────────────────────────

def test_miniatura_ml_vira_alta_resolucao():
    from core.foto_url import alta_resolucao

    mini = "https://http2.mlstatic.com/D_NQ_NP_811742-MLB123456789-I.jpg"
    assert alta_resolucao(mini) == (
        "https://http2.mlstatic.com/D_NQ_NP_2X_811742-MLB123456789-O.jpg"
    )


def test_alta_resolucao_nao_corrompe_nome_terminado_em_i():
    """`str.replace("I.jpg", "O.jpg")` (versão antiga) quebrava este caso."""
    from core.foto_url import alta_resolucao

    url = "https://http2.mlstatic.com/D_NQ_NP_2X_banner_MOBILE_UI.jpg"
    assert alta_resolucao(url).endswith("MOBILE_UI.jpg")


def test_alta_resolucao_forca_https_e_remove_fragmento():
    from core.foto_url import alta_resolucao

    url = "http://http2.mlstatic.com/D_NQ_NP_1-MLB2-V.jpg#tracking"
    saida = alta_resolucao(url)
    assert saida.startswith("https://")
    assert "#" not in saida


def test_variantes_sem_repeticao_e_na_ordem():
    from core.foto_url import variantes

    v = variantes("https://http2.mlstatic.com/D_NQ_NP_1-MLB2-I.jpg")
    assert v[0].endswith("-O.jpg") and "2X" in v[0]
    assert len(v) == len(set(v))


def test_amazon_remove_modificador_de_tamanho():
    from core.foto_url import alta_resolucao

    url = "https://m.media-amazon.com/images/I/71abc._AC_SX300_SY300_.jpg"
    assert alta_resolucao(url) == "https://m.media-amazon.com/images/I/71abc.jpg"


# ── Quarentena de publicação ─────────────────────────────────────────────────

def _db_temporario():
    """Aponta o módulo de banco para um arquivo novo e isolado."""
    import core.database as db

    tmp = tempfile.mkdtemp(prefix="bot_ofertas_test_")
    db._DB_PATH = os.path.join(tmp, "teste.db")
    db._falhas_tbl_checked = False
    db.inicializar()
    return db


def test_quarentena_apos_tres_falhas():
    """O caso MLB68674214: 3 falhas seguidas tiram o produto de rotação."""
    db = _db_temporario()
    pid = "MLB68674214"

    r1 = db.registrar_falha_publicacao(pid, "foto indisponível", "TV 32")
    assert r1["tentativas"] == 1 and r1["quarentena"] is False
    assert db.em_quarentena(pid) is False

    r2 = db.registrar_falha_publicacao(pid, "foto indisponível", "TV 32")
    assert r2["tentativas"] == 2 and r2["quarentena"] is False

    r3 = db.registrar_falha_publicacao(pid, "foto indisponível", "TV 32")
    assert r3["tentativas"] == 3 and r3["quarentena"] is True
    assert db.em_quarentena(pid) is True

    itens = db.listar_quarentena()
    assert [i["produto_id"] for i in itens] == [pid]


def test_publicacao_bem_sucedida_zera_o_contador():
    """Falha isolada de ontem não pode contar para o limite de hoje."""
    db = _db_temporario()
    pid = "MLB1"
    db.registrar_falha_publicacao(pid, "erro")
    db.registrar_falha_publicacao(pid, "erro")
    db.limpar_falha_publicacao(pid)
    assert db.registrar_falha_publicacao(pid, "erro")["tentativas"] == 1
    assert db.em_quarentena(pid) is False


def test_liberar_quarentena_manual():
    db = _db_temporario()
    for _ in range(3):
        db.registrar_falha_publicacao("MLB2", "erro")
    assert db.em_quarentena("MLB2") is True
    assert db.liberar_quarentena("MLB2") == 1
    assert db.em_quarentena("MLB2") is False


def test_quarentena_expira():
    """Quarentena é temporária: com prazo zero, o produto já volta."""
    db = _db_temporario()
    r = db.registrar_falha_publicacao("MLB3", "erro", max_tentativas=1,
                                      horas_quarentena=0)
    assert r["quarentena"] is True
    assert db.em_quarentena("MLB3") is False   # prazo já vencido


def test_produto_desconhecido_nao_esta_em_quarentena():
    db = _db_temporario()
    assert db.em_quarentena("NUNCA_VISTO") is False


# ── Pausa ────────────────────────────────────────────────────────────────────

def test_pausa_e_retomada():
    from core import pausa

    tmp = tempfile.mkdtemp(prefix="bot_pausa_")
    pausa.FLAG_PATH = os.path.join(tmp, "pausado.flag")

    assert pausa.pausado() is False
    assert pausa.info() == {}
    pausa.pausar("teste automatizado", origem="pytest")
    assert pausa.pausado() is True
    assert pausa.info()["motivo"] == "teste automatizado"
    assert pausa.retomar() is True
    assert pausa.pausado() is False
    assert pausa.retomar() is False   # idempotente


# ── Cliente n8n ──────────────────────────────────────────────────────────────

def test_n8n_desativado_por_padrao():
    """Sem N8N_WEBHOOK_URL, tudo vira no-op — o bot roda igual sem n8n."""
    from integrations import n8n

    antes = os.environ.pop("N8N_WEBHOOK_URL", None)
    try:
        assert n8n.ativo() is False
        assert n8n.emitir("teste", {"a": 1}) is False
    finally:
        if antes is not None:
            os.environ["N8N_WEBHOOK_URL"] = antes


def test_assinatura_hmac_confere():
    from integrations import n8n

    corpo = json.dumps({"evento": "teste"}, ensure_ascii=False).encode("utf-8")
    assinatura = n8n.assinar(corpo, token="segredo")
    assert assinatura.startswith("sha256=")
    assert n8n.conferir_assinatura(corpo, assinatura, token="segredo") is True
    assert n8n.conferir_assinatura(corpo, assinatura, token="outro") is False
    assert n8n.conferir_assinatura(b"outro corpo", assinatura, token="segredo") is False
    assert n8n.conferir_assinatura(corpo, "", token="segredo") is False


def test_comando_desconhecido_nao_levanta():
    from integrations.n8n_commands import executar

    r = executar("apagar_tudo", {})
    assert r["ok"] is False
    assert "disponiveis" in r


def test_comando_ping():
    from integrations.n8n_commands import executar

    r = executar("ping", {"eco": "oi"})
    assert r["ok"] is True
    assert r["resultado"]["eco"] == "oi"


# ── Workflows do n8n ─────────────────────────────────────────────────────────

def _workflows():
    pasta = os.path.join(BASE, "n8n", "workflows")
    for nome in sorted(os.listdir(pasta)):
        if nome.endswith(".json"):
            with open(os.path.join(pasta, nome), encoding="utf-8") as f:
                yield nome, json.load(f)


def test_workflows_tem_estrutura_valida():
    achou = False
    for nome, wf in _workflows():
        achou = True
        assert wf.get("name"), f"{nome}: sem nome"
        assert wf.get("nodes"), f"{nome}: sem nós"
        nomes = {n["name"] for n in wf["nodes"]}
        assert len(nomes) == len(wf["nodes"]), f"{nome}: nós com nome repetido"
        for origem, saidas in wf.get("connections", {}).items():
            assert origem in nomes, f"{nome}: conexão a partir de nó inexistente '{origem}'"
            for saida in saidas["main"]:
                for c in saida:
                    assert c["node"] in nomes, f"{nome}: conexão para nó inexistente '{c['node']}'"
    assert achou, "nenhum workflow encontrado em n8n/workflows/"


def test_workflows_nao_contem_segredo():
    """Nenhum token pode vazar para dentro do JSON versionado."""
    suspeitos = ("accessToken", "api_key", "apiKey", "bot1", "AAF", "AAG")
    for nome, wf in _workflows():
        bruto = json.dumps(wf, ensure_ascii=False)
        for s in suspeitos:
            assert s not in bruto, f"{nome}: possível segredo embutido ({s})"


def test_setup_preenche_config_apenas_quando_vazio():
    sys.path.insert(0, os.path.join(BASE, "n8n"))
    from setup_n8n import preencher_config

    js = "const CONFIG = { admin_chat_id: '', canal: '@antigo' };"
    saida = preencher_config(js, {"admin_chat_id": "123", "canal": "@novo"})
    assert "admin_chat_id: '123'" in saida
    assert "canal: '@antigo'" in saida   # já preenchido: não sobrescreve


def test_setup_envia_apenas_campos_aceitos_pela_api():
    sys.path.insert(0, os.path.join(BASE, "n8n"))
    from setup_n8n import preparar_workflow

    _, wf = next(iter(_workflows()))
    corpo = preparar_workflow(wf, {"Bot Ofertas — Telegram": "ID1"}, {})
    assert set(corpo) <= {"name", "nodes", "connections", "settings"}
    assert "tags" not in corpo   # a API pública recusa com 400


# ── Divulgação ───────────────────────────────────────────────────────────────

def test_anuncio_do_grupo_traz_os_dois_links():
    from core import divulgacao

    texto = divulgacao.texto_grupo("instagram")
    assert "t.me/ofertaseletronics" in texto
    assert "chat.whatsapp.com" in texto
    assert "utm_source=instagram" in texto


def test_gerar_devolve_texto_mesmo_sem_ofertas():
    """Divulgação nunca fica um dia sem conteúdo por rodada fraca."""
    from core import divulgacao

    r = divulgacao.gerar("tiktok", "grupo")
    assert r["texto"] and r["rede"] == "tiktok"


def test_post_de_oferta_marca_a_rede_no_link():
    from core import divulgacao

    produto = {
        "titulo": "Smart TV 50 4K", "preco": 1899.0, "preco_original": 2999.0,
        "desconto_pct": 36.0,
        "affiliate_link": "https://www.mercadolivre.com.br/p/MLB7?matt_tool=47114387",
    }
    texto = divulgacao.texto_oferta(produto, "facebook")
    assert "matt_source=facebook" in texto
    assert "matt_tool=47114387" in texto
    assert "t.me/ofertaseletronics" in texto


if __name__ == "__main__":
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
