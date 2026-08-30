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
import inspect
import os
import re
import shutil
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


def test_alta_resolucao_funciona_com_query_na_url():
    """As duas regexes de variante terminam em `$`. Rodando sobre a URL
    inteira, uma foto com query (`...-I.jpg?v=2`) nao casava: o `-I` da
    miniatura sobrevivia e o produto era publicado com a miniatura — o
    defeito que este modulo existe para corrigir. E o prefixo `D_NQ_NP_2X_`
    era aplicado assim mesmo, montando uma combinacao que o CDN nao serve.
    """
    from core.foto_url import alta_resolucao

    saida = alta_resolucao("https://http2.mlstatic.com/D_NQ_NP_811742-MLB123-I.jpg?v=2")
    assert saida == "https://http2.mlstatic.com/D_NQ_NP_2X_811742-MLB123-O.jpg?v=2", saida


def test_host_de_foto_nao_casa_por_sufixo_solto():
    """`net.endswith("mlstatic.com")` casa `evil-mlstatic.com`: um dominio
    de terceiro receberia as reescritas de URL do CDN do ML."""
    from core.foto_url import _HOSTS_ML, _e_host

    assert _e_host("https://http2.mlstatic.com/x-I.jpg", _HOSTS_ML)
    assert _e_host("https://mlstatic.com/x-I.jpg", _HOSTS_ML)
    assert not _e_host("https://evil-mlstatic.com/x-I.jpg", _HOSTS_ML)
    assert not _e_host("https://mlstatic.com.br/x-I.jpg", _HOSTS_ML)


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


# ── Token do Mercado Livre ───────────────────────────────────────────────────

def _limpar_env_ml():
    from core import ml_token

    antes = {k: os.environ.pop(k, None)
             for k in ("ML_APP_ID", "ML_APP_SECRET", "ML_ACCESS_TOKEN")}
    ml_token.invalidar()
    return antes


def _restaurar_env_ml(antes: dict):
    from core import ml_token

    for k, v in antes.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    ml_token.invalidar()


def test_ml_token_sem_credenciais_levanta():
    from core.ml_token import SemCredenciaisML, token

    antes = _limpar_env_ml()
    try:
        try:
            token()
        except SemCredenciaisML as e:
            assert "ML_APP_ID" in str(e)
        else:
            raise AssertionError("deveria ter levantado SemCredenciaisML")
    finally:
        _restaurar_env_ml(antes)


def test_ml_token_usa_token_fixo_quando_nao_ha_app():
    """Compatibilidade com quem já rodou `python ml_auth.py`."""
    from core.ml_token import token

    antes = _limpar_env_ml()
    try:
        os.environ["ML_ACCESS_TOKEN"] = "TOKEN-FIXO-123"
        assert token() == "TOKEN-FIXO-123"
    finally:
        _restaurar_env_ml(antes)


def test_ml_token_reaproveita_cache_valido():
    """Token em cache e dentro da validade não dispara nova requisição."""
    import time

    from core import ml_token

    antes = _limpar_env_ml()
    try:
        ml_token._cache["token"] = "EM-CACHE"
        ml_token._cache["expira_em"] = time.time() + 3600
        assert ml_token.token() == "EM-CACHE"
        assert ml_token.status()["em_cache"] is True
        assert ml_token.status()["expira_em_min"] > 50
    finally:
        _restaurar_env_ml(antes)


def test_ml_token_invalidar_descarta_cache():
    import time

    from core import ml_token

    antes = _limpar_env_ml()
    try:
        ml_token._cache["token"] = "VELHO"
        ml_token._cache["expira_em"] = time.time() + 3600
        ml_token.invalidar()
        assert ml_token.status()["em_cache"] is False
    finally:
        _restaurar_env_ml(antes)


def test_ml_token_cabecalho_no_formato_bearer():
    from core.ml_token import cabecalhos

    antes = _limpar_env_ml()
    try:
        os.environ["ML_ACCESS_TOKEN"] = "ABC"
        assert cabecalhos() == {"Authorization": "Bearer ABC"}
    finally:
        _restaurar_env_ml(antes)


# ── Configuração assistida do .env ───────────────────────────────────────────

def _setup_module():
    sys.path.insert(0, os.path.join(BASE, "n8n"))
    import setup_n8n
    return setup_n8n


def test_gravar_env_preserva_comentarios_e_ordem():
    """Reescrever o .env inteiro perderia os comentários que explicam cada
    campo — e é neles que o Daniel se apoia para configurar."""
    setup = _setup_module()
    tmp = tempfile.mkdtemp(prefix="env_")
    caminho = os.path.join(tmp, ".env")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("# Telegram\nTOKEN_TELEGRAM=abc\n\n# n8n\nN8N_TOKEN=\n")

    original = setup.ENV_PATH
    setup.ENV_PATH = caminho
    try:
        setup.gravar_no_env({"N8N_TOKEN": "novo-segredo"})
        conteudo = open(caminho, encoding="utf-8").read()
    finally:
        setup.ENV_PATH = original

    assert "# Telegram" in conteudo and "# n8n" in conteudo
    assert "TOKEN_TELEGRAM=abc" in conteudo
    assert "N8N_TOKEN=novo-segredo" in conteudo
    assert conteudo.count("N8N_TOKEN=") == 1     # substituiu, não duplicou


def test_gravar_env_acrescenta_chave_nova_no_fim():
    setup = _setup_module()
    tmp = tempfile.mkdtemp(prefix="env_")
    caminho = os.path.join(tmp, ".env")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("TOKEN_TELEGRAM=abc\n")

    original = setup.ENV_PATH
    setup.ENV_PATH = caminho
    try:
        alterados = setup.gravar_no_env({"ADMIN_CHAT_ID": "555"})
        conteudo = open(caminho, encoding="utf-8").read()
    finally:
        setup.ENV_PATH = original

    assert alterados == ["ADMIN_CHAT_ID"]
    assert "ADMIN_CHAT_ID=555" in conteudo
    assert "TOKEN_TELEGRAM=abc" in conteudo


def test_gravar_env_nao_toca_em_linha_comentada():
    """`# N8N_TOKEN=exemplo` é documentação, não configuração."""
    setup = _setup_module()
    tmp = tempfile.mkdtemp(prefix="env_")
    caminho = os.path.join(tmp, ".env")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("# N8N_TOKEN=exemplo-nao-usar\n")

    original = setup.ENV_PATH
    setup.ENV_PATH = caminho
    try:
        setup.gravar_no_env({"N8N_TOKEN": "real"})
        conteudo = open(caminho, encoding="utf-8").read()
    finally:
        setup.ENV_PATH = original

    assert "# N8N_TOKEN=exemplo-nao-usar" in conteudo   # comentário intacto
    assert "\nN8N_TOKEN=real" in conteudo               # chave real acrescentada


def test_env_ausente_parte_do_exemplo():
    """Sem .env, gravar só as chaves geradas produziria um arquivo de 4
    linhas — sem TOKEN_TELEGRAM e sem os comentários. O bot nem subiria."""
    setup = _setup_module()
    tmp = tempfile.mkdtemp(prefix="env_")
    exemplo = os.path.join(tmp, ".env.example")
    with open(exemplo, "w", encoding="utf-8") as f:
        f.write("# Telegram\nTOKEN_TELEGRAM=cole_aqui\n# n8n\nN8N_TOKEN=\n")

    orig_env, orig_ex = setup.ENV_PATH, setup.ENV_EXEMPLO_PATH
    setup.ENV_PATH = os.path.join(tmp, ".env")
    setup.ENV_EXEMPLO_PATH = exemplo
    try:
        setup.gravar_no_env({"N8N_TOKEN": "gerado"})
        conteudo = open(setup.ENV_PATH, encoding="utf-8").read()
    finally:
        setup.ENV_PATH, setup.ENV_EXEMPLO_PATH = orig_env, orig_ex

    assert "TOKEN_TELEGRAM=cole_aqui" in conteudo   # campo do exemplo veio junto
    assert "# Telegram" in conteudo                 # comentário preservado
    assert "N8N_TOKEN=gerado" in conteudo
    assert conteudo.count("N8N_TOKEN=") == 1


def test_descobrir_chat_id_extrai_chats_do_getupdates():
    """Poupa o passo de caçar `chat.id` no JSON cru do getUpdates."""
    setup = _setup_module()

    resposta = json.dumps({"ok": True, "result": [
        {"message": {"chat": {"id": 555000111, "type": "private",
                              "first_name": "Daniel", "last_name": "Silva"}}},
        {"message": {"chat": {"id": 555000111, "type": "private",
                              "first_name": "Daniel"}}},          # repetido
        {"channel_post": {"chat": {"id": -1002222, "type": "channel",
                                   "title": "Ofertas Eletronics"}}},
    ]}).encode()

    class _Resp:
        def read(self): return resposta
        def __enter__(self): return self
        def __exit__(self, *a): return False

    original = setup.request.urlopen
    setup.request.urlopen = lambda *a, **k: _Resp()
    try:
        achados = dict(setup.descobrir_chat_id("123:FAKE"))
    finally:
        setup.request.urlopen = original

    assert len(achados) == 2                       # deduplicado
    assert "Daniel Silva" in achados["555000111"]
    assert "Ofertas Eletronics" in achados["-1002222"]


def test_descobrir_chat_id_sem_token_nao_chama_rede():
    setup = _setup_module()
    assert setup.descobrir_chat_id("") == []


def test_configurar_avisa_que_falta_admin_chat_id():
    """Bug real (2026-08-29, achado na saída do Daniel): o resumo do
    `--configurar` só conferia N8N_API_KEY e TOKEN_TELEGRAM. Com o
    ADMIN_CHAT_ID vazio o comando dizia que faltava apenas a API key, o
    `--importar` completava sem erro e os workflows subiam ativos — mas
    nenhum alerta chegava a ninguém, incluindo o "o PC nao religou" que
    sustenta a publicação diária. Uma config incompleta que se apresenta
    como completa custa mais que uma que falha na cara.
    """
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.TemporaryDirectory(prefix="bot_cfg_") as tmp:
        for pasta in ("core", "n8n"):
            shutil.copytree(os.path.join(raiz, pasta), os.path.join(tmp, pasta))
        shutil.copy(os.path.join(raiz, ".env.example"), tmp)
        # .env como o de produção: token do Telegram real, sem chat_id.
        with open(os.path.join(raiz, ".env.example"), encoding="utf-8") as f:
            base = f.read().replace("TOKEN_TELEGRAM=cole_aqui_o_token_do_BotFather",
                                    "TOKEN_TELEGRAM=123456:FAKE-token-de-teste")
        with open(os.path.join(tmp, ".env"), "w", encoding="utf-8") as f:
            f.write(base)

        ambiente = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        for chave in ("ADMIN_CHAT_ID", "ADMIN_IDS", "N8N_API_KEY", "N8N_TOKEN"):
            ambiente.pop(chave, None)
        r = subprocess.run([sys.executable, os.path.join("n8n", "setup_n8n.py"), "--configurar"],
                           cwd=tmp, capture_output=True, text=True, timeout=120, env=ambiente)

    saida = r.stdout
    assert "ADMIN_CHAT_ID" in saida.split("Ainda falta preencher:")[-1], saida
    assert "NENHUM alerta sai" in saida, saida
    # Exit != 0 é o que permite encadear os comandos sem seguir com uma
    # configuração pela metade.
    assert r.returncode != 0, saida


def test_configurar_liga_comandos_remotos_so_com_n8n_local():
    """O caminho de volta (n8n -> bot) e uma decisao de exposicao de rede.

    Com o n8n na MESMA maquina, `127.0.0.1:8724` ja e alcancavel: ligar o
    BOT_API_URL nao abre porta nenhuma, so aproveita um canal que existia.
    Com o n8n fora dela, o mesmo campo exigiria expor na rede um endpoint
    que PAUSA a operacao — isso nunca pode ser ligado por conta propria.

    Este teste trava as duas metades: sem ele, uma "melhoria" futura que
    preenchesse o campo sempre passaria despercebida e exporia a API.
    """
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def rodar(api_url: str) -> tuple[str, str]:
        with tempfile.TemporaryDirectory(prefix="bot_volta_") as tmp:
            for pasta in ("core", "n8n"):
                shutil.copytree(os.path.join(raiz, pasta), os.path.join(tmp, pasta))
            with open(os.path.join(raiz, ".env.example"), encoding="utf-8") as f:
                base = f.read()
            base = base.replace("TOKEN_TELEGRAM=cole_aqui_o_token_do_BotFather",
                                "TOKEN_TELEGRAM=123456:FAKE")
            base = re.sub(r"(?m)^N8N_API_URL=.*$", f"N8N_API_URL={api_url}", base)
            with open(os.path.join(tmp, ".env"), "w", encoding="utf-8") as f:
                f.write(base)
            ambiente = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            for chave in ("BOT_API_URL", "N8N_TOKEN", "N8N_API_URL", "ADMIN_CHAT_ID"):
                ambiente.pop(chave, None)
            r = subprocess.run(
                [sys.executable, os.path.join("n8n", "setup_n8n.py"), "--configurar"],
                cwd=tmp, capture_output=True, text=True, timeout=120, env=ambiente)
            with open(os.path.join(tmp, ".env"), encoding="utf-8") as f:
                env_final = f.read()
        return r.stdout, env_final

    saida, env_final = rodar("http://localhost:5678")
    assert "BOT_API_URL=http://127.0.0.1:8724" in env_final, saida
    assert "libera os comandos remotos" in saida, saida
    # E a exposicao de rede continua exatamente onde estava.
    assert re.search(r"(?m)^HEALTHCHECK_BIND=127\.0\.0\.1$", env_final), (
        "o bind saiu de 127.0.0.1 — isso expoe /n8n/comando na rede local")

    saida, env_final = rodar("https://daniel.app.n8n.cloud")
    assert re.search(r"(?m)^BOT_API_URL=\s*$", env_final), (
        "n8n fora da maquina nao pode ligar o caminho de volta sozinho:\n" + saida)
    assert "decisão sua" in saida or "decisao sua" in saida, saida


def test_preparar_credenciais_bate_com_o_header_que_o_bot_envia():
    """O nome do header é o ponto onde a instalação manual mais erra.

    Se a credencial de Header Auth for criada com qualquer coisa diferente
    de `X-Bot-Token`, o nó Webhook recusa TODO evento com 403 e o n8n não
    diz por quê — o bot fica publicando normalmente e o histórico na nuvem
    fica vazio. Este teste amarra o gerador de credenciais ao valor que o
    `integrations/n8n.py` realmente manda, para os dois não se separarem.
    """
    import tempfile  # noqa: PLC0415

    setup = _setup_module()
    guardado = {k: os.environ.get(k) for k in ("TOKEN_TELEGRAM", "N8N_TOKEN")}
    os.environ["TOKEN_TELEGRAM"] = "123456:FAKE"
    os.environ["N8N_TOKEN"] = "segredo-do-webhook"
    try:
        with tempfile.TemporaryDirectory(prefix="bot_cred_") as d:
            destino = os.path.join(d, "cred.json")
            assert setup.preparar_credenciais(destino) == 0
            with open(destino, encoding="utf-8") as f:
                creds = json.load(f)

        por_tipo = {c["type"]: c for c in creds}
        assert set(por_tipo) == {"telegramApi", "httpHeaderAuth"}, por_tipo

        header = por_tipo["httpHeaderAuth"]
        assert header["data"]["name"] == "X-Bot-Token"
        assert header["data"]["value"] == "segredo-do-webhook"
        assert por_tipo["telegramApi"]["data"]["accessToken"] == "123456:FAKE"

        # E o nome do header tem que ser o mesmo que o cliente envia.
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(raiz, "integrations", "n8n.py"), encoding="utf-8") as f:
            cliente = f.read()
        assert '"X-Bot-Token"' in cliente, (
            "o cliente parou de mandar X-Bot-Token; a credencial gerada ficou orfa")

        # Os nomes precisam bater com os que os workflows referenciam.
        assert creds[0]["name"] == setup.CRED_TELEGRAM
        assert creds[1]["name"] == setup.CRED_HEADER

        # O `id` nao e opcional: sem ele o import morre com
        # "SQLITE_CONSTRAINT: NOT NULL constraint failed:
        # credentials_entity.id" (reproduzido no n8n 2.35.7).
        assert creds[0]["id"] == setup.CRED_ID_TELEGRAM
        assert creds[1]["id"] == setup.CRED_ID_HEADER
    finally:
        for k, v in guardado.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_preparar_credenciais_recusa_sem_segredos():
    """Gerar um arquivo de credenciais vazio seria pior que falhar: ele
    importaria sem erro e deixaria o webhook sem autenticação."""
    import tempfile  # noqa: PLC0415

    setup = _setup_module()
    guardado = {k: os.environ.get(k) for k in ("TOKEN_TELEGRAM", "N8N_TOKEN")}
    os.environ.pop("TOKEN_TELEGRAM", None)
    os.environ.pop("N8N_TOKEN", None)
    try:
        with tempfile.TemporaryDirectory(prefix="bot_cred_") as d:
            destino = os.path.join(d, "cred.json")
            assert setup.preparar_credenciais(destino) == 1
            assert not os.path.exists(destino), "nao pode deixar arquivo pela metade"
    finally:
        for k, v in guardado.items():
            if v is not None:
                os.environ[k] = v


def test_preparar_grava_workflows_prontos_sem_rede():
    """`--preparar` é o caminho de instalação que não precisa de API key.

    A instalação real travou por não achar a tela da API key numa interface
    que muda de versão para versão; sem uma saída por arquivo, o único
    caminho restante era manual e nada garantia que o `admin_chat_id` fosse
    parar lá dentro. O que este comando grava tem que ser byte a byte o que
    o `--importar` enviaria, e sem tocar na rede — senão vira um segundo
    caminho que envelhece diferente do primeiro.
    """
    import tempfile  # noqa: PLC0415

    setup = _setup_module()
    original = setup.request.urlopen

    def _proibido(*a, **k):  # noqa: ANN002
        raise AssertionError("--preparar não pode tocar na rede")

    setup.request.urlopen = _proibido
    antes = os.environ.get("ADMIN_CHAT_ID")
    os.environ["ADMIN_CHAT_ID"] = "555000111"
    try:
        with tempfile.TemporaryDirectory(prefix="bot_prontos_") as destino:
            assert setup.preparar_para_importar(destino) == 0
            nomes = sorted(n for n in os.listdir(destino) if n.endswith(".json"))
            assert len(nomes) == 5, nomes
            for nome in nomes:
                with open(os.path.join(destino, nome), encoding="utf-8") as f:
                    pronto = json.load(f)
                # Só os campos que a API e a CLI aceitam; `id`/`active`/`tags`
                # fazem a importação ser recusada.
                assert sorted(pronto) == ["connections", "name", "nodes", "settings"], nome
                # Mesma preparação do caminho pela API — com uma diferença
                # deliberada: lá as credenciais acabaram de ser criadas e
                # têm ids sorteados pelo n8n; aqui elas vêm de
                # `--preparar-credenciais`, com os ids fixos. O resto
                # (CONFIG, janela, campos aceitos) tem que ser idêntico.
                ids = {setup.CRED_TELEGRAM: setup.CRED_ID_TELEGRAM,
                       setup.CRED_HEADER: setup.CRED_ID_HEADER}
                with open(os.path.join(setup.DIR_WORKFLOWS, nome), encoding="utf-8") as f:
                    esperado = setup.preparar_workflow(json.load(f), ids, setup._valores_config())
                assert pronto == esperado, nome
            with open(os.path.join(destino, "01-ingestao-e-watchdog.json"), encoding="utf-8") as f:
                bruto = f.read()
            assert "admin_chat_id: '555000111'" in bruto
            assert "silencio_ate: '08:30'" in bruto

            # Bug real: os workflows saiam apontando para o placeholder
            # `REPLACE_TELEGRAM_CRED`. A importacao dizia "Successfully
            # imported", o workflow aparecia certinho na tela, e so na
            # execucao o no do Telegram falhava por credencial inexistente
            # — o n8n resolve credencial por ID, nao pelo nome.
            assert "REPLACE_" not in bruto, "placeholder de credencial sobreviveu"
            for nome_wf in nomes:
                with open(os.path.join(destino, nome_wf), encoding="utf-8") as f:
                    wf_pronto = json.load(f)
                for no in wf_pronto["nodes"]:
                    for tipo, ref in (no.get("credentials") or {}).items():
                        esperado = (setup.CRED_ID_TELEGRAM if tipo == "telegramApi"
                                    else setup.CRED_ID_HEADER)
                        assert ref.get("id") == esperado, (nome_wf, no["name"], ref)
    finally:
        setup.request.urlopen = original
        if antes is None:
            os.environ.pop("ADMIN_CHAT_ID", None)
        else:
            os.environ["ADMIN_CHAT_ID"] = antes


def test_configurar_nao_conta_placeholder_como_preenchido():
    """`cole_aqui_o_token_do_BotFather` vindo do .env.example passava por
    valor preenchido — e o erro só aparecia depois, como uma resposta
    obscura da API do Telegram."""
    setup = _setup_module()
    fonte = inspect.getsource(setup.configurar)
    assert "cole_aqui" in fonte and "startswith" in fonte, (
        "a checagem de placeholder sumiu de configurar()")


# ── Spool: a promessa de nao perder evento em queda de rede ─────────────────

def _n8n_com_spool_temporario():
    """Importa o cliente do n8n apontando o spool para um arquivo descartavel.

    Nunca usar o caminho de producao aqui: um teste que reescreve
    `data/n8n_spool.jsonl` apagaria eventos reais que ainda nao sairam.
    """
    os.environ["N8N_WEBHOOK_URL"] = "https://exemplo.invalid/webhook/teste"
    os.environ["N8N_ATIVO"] = "1"
    from integrations import n8n

    n8n.SPOOL_PATH = os.path.join(tempfile.mkdtemp(prefix="n8n_spool_"), "spool.jsonl")
    return n8n


def test_flush_nao_apaga_evento_que_chegou_durante_o_envio():
    """O spool existe para NAO perder evento em queda de rede — e era
    justamente durante a rede lenta que ele perdia.

    `flush_spool()` lia a lista de eventos, gastava segundos no POST e depois
    reescrevia o arquivo com `linhas[enviados:]` — calculado sobre a lista
    VELHA. Um evento que falhasse nesse meio-tempo (cada `emitir()` roda na
    sua thread) era acrescentado ao arquivo e apagado pela reescrita.
    """
    n8n = _n8n_com_spool_temporario()
    original = n8n._postar
    try:
        n8n._guardar_no_spool({"evento": "antigo"})

        def postar_e_receber_outro(payload):
            # Enquanto o POST acontece, outro evento falha e entra no spool.
            n8n._guardar_no_spool({"evento": "novo_que_falhou"})
            return True

        n8n._postar = postar_e_receber_outro
        n8n.flush_spool()

        with open(n8n.SPOOL_PATH, encoding="utf-8") as f:
            sobraram = [json.loads(linha)["evento"] for linha in f if linha.strip()]
        assert sobraram == ["novo_que_falhou"], sobraram
    finally:
        n8n._postar = original


def test_dois_flushes_simultaneos_nao_reenviam_o_mesmo_evento():
    """Cada `emitir()` que da certo chama `flush_spool()`. Dois eventos
    terminando juntos liam o mesmo spool e reenviavam os mesmos eventos —
    o outro lado recebia alerta (e publicacao de reforco) em duplicata."""
    import threading
    import time

    n8n = _n8n_com_spool_temporario()
    original = n8n._postar
    try:
        n8n._guardar_no_spool({"evento": "oferta_publicada"})
        enviados = []

        def postar_lento(payload):
            time.sleep(0.15)  # a janela em que o segundo flush entrava
            enviados.append(payload["evento"])
            return True

        n8n._postar = postar_lento
        threads = [threading.Thread(target=n8n.flush_spool) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert enviados == ["oferta_publicada"], enviados
    finally:
        n8n._postar = original


def test_gravar_env_e_atomico_e_guarda_backup():
    """O `.env` e o arquivo mais critico do projeto: sem ele o bot nao sobe
    e o TOKEN_TELEGRAM tem de ser gerado de novo no BotFather. Abrir o
    proprio arquivo em "w" o trunca ANTES de escrever — queda de energia,
    Ctrl+C ou disco cheio no meio deixavam o .env pela metade."""
    setup = _setup_module()
    d = tempfile.mkdtemp(prefix="env_atomico_")
    env_antes, exemplo_antes = setup.ENV_PATH, setup.ENV_EXEMPLO_PATH
    try:
        setup.ENV_PATH = os.path.join(d, ".env")
        setup.ENV_EXEMPLO_PATH = os.path.join(d, ".env.example")
        with open(setup.ENV_PATH, "w", encoding="utf-8") as f:
            f.write("# comentario que nao pode sumir\nTOKEN_TELEGRAM=antigo\n")

        setup.gravar_no_env({"TOKEN_TELEGRAM": "novo", "N8N_TOKEN": "abc"})

        final = open(setup.ENV_PATH, encoding="utf-8").read()
        assert "TOKEN_TELEGRAM=novo" in final and "N8N_TOKEN=abc" in final, final
        assert "comentario que nao pode sumir" in final, final

        backup = setup.ENV_PATH + ".bak"
        assert os.path.exists(backup), "sem copia da versao anterior"
        assert "TOKEN_TELEGRAM=antigo" in open(backup, encoding="utf-8").read()

        sobras = [f for f in os.listdir(d) if ".env.novo." in f]
        assert not sobras, f"temporario deixado para tras: {sobras}"
    finally:
        setup.ENV_PATH, setup.ENV_EXEMPLO_PATH = env_antes, exemplo_antes


def test_backup_do_env_esta_no_gitignore():
    """O `.env.bak` carrega TOKEN_TELEGRAM e N8N_TOKEN em claro, e este
    repositorio e a pagina publica do projeto: um `git add -A` na maquina do
    Daniel publicaria os dois."""
    ignore = open(os.path.join(BASE, ".gitignore"), encoding="utf-8").read().splitlines()
    regras = {linha.strip() for linha in ignore}
    for padrao in (".env", ".env.bak", ".env.novo.*"):
        assert padrao in regras, f"{padrao} fora do .gitignore"


def test_os_tres_publicadores_usam_o_cliente_com_timeout():
    """O read timeout padrao da biblioteca (5s) e curto demais para um
    `send_photo` com upload numa conexao domestica — foi assim que a rodada
    das 23:20 de 25/08 morreu com um "Timed out" seco.

    A correcao tinha ficado so no rastreador.py; Amazon e campanha de
    ferramentas seguiam abrindo `Bot(token=...)` cru, ou seja, dois dos tres
    caminhos de publicacao mantinham o defeito. Este teste roda sem o pacote
    `telegram` instalado (o CI nao o instala) porque olha o codigo-fonte.
    """
    import re as _re

    publicadores = ("rastreador.py", "rastreador_amazon.py", "campanha_ferramentas.py")
    for nome in publicadores:
        fonte = open(os.path.join(BASE, nome), encoding="utf-8").read()
        # Ignora as linhas de comentario, que citam o padrao antigo de proposito.
        codigo = "\n".join(l for l in fonte.splitlines() if not l.strip().startswith("#"))
        cru = _re.findall(r"Bot\(\s*token\s*=", codigo)
        assert not cru, f"{nome} abre Bot(token=...) direto, sem os timeouts: {cru}"
        assert "criar_bot" in codigo, f"{nome} nao usa criar_bot()"

    tg = open(os.path.join(BASE, "integrations", "telegram_bot.py"), encoding="utf-8").read()
    assert "def criar_bot" in tg, "criar_bot() saiu de integrations/telegram_bot.py"
    assert "TELEGRAM_TIMEOUT_LEITURA" in tg, "o timeout deixou de ser configuravel pelo .env"


def test_criar_bot_aplica_o_timeout_de_verdade():
    """Conferir no objeto, nao no codigo: `Bot(request=...)` guarda DOIS
    clientes — o de `get_updates` e o das chamadas normais. Olhar o primeiro
    (que segue no padrao de 5s de proposito) faria o teste passar mesmo se o
    cliente configurado nao estivesse sendo usado para publicar."""
    try:
        from integrations.telegram_bot import criar_bot
    except ImportError:
        return  # sem o pacote `telegram` (caso do CI): a checagem de fonte cobre

    bot = criar_bot("123:fake")
    timeouts = [getattr(r, "_client_kwargs", {}).get("timeout") for r in bot._request]
    leituras = [getattr(t, "read", None) for t in timeouts if t is not None]
    assert 40.0 in leituras, f"nenhum cliente com read timeout de 40s: {timeouts}"


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
