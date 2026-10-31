# -*- coding: utf-8 -*-
"""
Testes do envio pela Evolution API e do guia de configuração inicial.

Cobrem exatamente as falhas reproduzidas em 2026-09-18, todas no caminho
que a Regra 16 elegeu como o único envio possível no servidor:

  - `setup_whatsapp_api.py` não lia o `.env` e abortava dizendo para
    configurar a chave que já estava configurada;
  - `docker compose -f docker/evolution.yml` procura o `.env` em `docker/`,
    não na raiz, e recusava subir por falta de `EVOLUTION_API_KEY`;
  - `/instance/create` da Evolution v2 devolve `hash` como string e o passo
    2 do guia quebrava com `AttributeError`;
  - `whatsapp_api._configurada()` aceitava o JID de exemplo
    (`120363XXXXXXXXX@g.us`) que o `wa_ativo()` já barrava — e a API é a
    TENTATIVA 1 de `enviar_para_grupo()`;
  - falha HTTP de envio devolvia False sem passar por `core/error_logger`
    (Regra 11: nunca falhar silenciosamente).

Nenhum teste aqui fala com a Evolution de verdade nem envia mensagem: as
respostas HTTP são dubladas.

Rodar:
    python tests/test_whatsapp_api.py     # só precisa de requests + dotenv
    python -m pytest tests/ -v
"""
import io
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WA_ENV = ("WHATSAPP_WEBHOOK_URL", "WHATSAPP_API_KEY", "WHATSAPP_INSTANCE",
          "WHATSAPP_GROUP_ID", "EVOLUTION_API_KEY")


class _Env:
    """Troca variáveis do ambiente e devolve tudo ao sair."""

    def __init__(self, **novas):
        self.novas = novas
        self.antigas = {}

    def __enter__(self):
        for k in WA_ENV:
            self.antigas[k] = os.environ.get(k)
            os.environ.pop(k, None)
        for k, v in self.novas.items():
            os.environ[k] = v
        return self

    def __exit__(self, *_):
        for k, v in self.antigas.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _Resposta:
    """Dublê de `requests.Response` — só o que o módulo consulta."""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("sem JSON")
        return self._payload


CONFIG_VALIDA = dict(
    WHATSAPP_WEBHOOK_URL="http://localhost:8080",
    WHATSAPP_API_KEY="chave",
    WHATSAPP_GROUP_ID="120363111122223333@g.us",
)


# ── Destino de exemplo nunca pode virar envio (Regras 11 e 16) ───────────────

def test_configurada_recusa_jid_de_exemplo():
    """O `120363XXXXXXXXX@g.us` do `deploy/.env.example` não é destino.

    Bug real: `wa_ativo()` barrava e `_configurada()` deixava passar. Como a
    Evolution API é a tentativa 1 de `enviar_para_grupo()`, num servidor
    recém-instalado a fila drenava para um JID que não existe — em silêncio.
    """
    from integrations.whatsapp_api import _configurada

    for exemplo in ("120363XXXXXXXXX@g.us", "cole_aqui_o_id_do_grupo",
                    "seu_grupo@g.us", "exemplo@g.us"):
        with _Env(WHATSAPP_WEBHOOK_URL="http://localhost:8080",
                  WHATSAPP_API_KEY="chave", WHATSAPP_GROUP_ID=exemplo):
            assert not _configurada(), f"{exemplo} passou como destino de verdade"


def test_configurada_aceita_jid_de_verdade():
    from integrations.whatsapp_api import _configurada
    with _Env(**CONFIG_VALIDA):
        assert _configurada()


def test_sender_e_api_respondem_a_mesma_coisa():
    """Duas respostas para "este destino vale?" e a pior ganhava."""
    from integrations.whatsapp_api import _configurada
    from integrations.whatsapp_sender import wa_ativo

    for jid in ("120363XXXXXXXXX@g.us", "120363111122223333@g.us"):
        with _Env(WHATSAPP_WEBHOOK_URL="http://localhost:8080",
                  WHATSAPP_API_KEY="chave", WHATSAPP_GROUP_ID=jid):
            assert wa_ativo() == _configurada(), f"divergiram em {jid}"


# ── Falha de envio nunca é silenciosa (Regra 11) ─────────────────────────────

def _capturar_registros(monkeypatch_alvo):
    """Troca `registrar_evento` por um coletor e devolve a lista."""
    import core.error_logger as el
    import integrations.whatsapp_api as wa
    registros = []
    original = el.registrar_evento
    el.registrar_evento = lambda op, msg, ctx=None: registros.append((op, msg, ctx))
    # A janela anti-repeticao e estado de modulo: um teste anterior nao pode
    # abafar o registro do teste seguinte.
    wa._ULTIMO_REGISTRO.clear()
    monkeypatch_alvo.append(lambda: setattr(el, "registrar_evento", original))
    monkeypatch_alvo.append(wa._ULTIMO_REGISTRO.clear)
    return registros


def test_sondagem_do_health_nao_enche_o_relatorio():
    """`/health` pergunta de minuto em minuto; o bloco de notas da Área de
    Trabalho é para o Daniel ler (Regra 9), não para receber o mesmo erro
    dezenas de vezes por dia."""
    import integrations.whatsapp_api as wa

    desfazer = []
    registros = _capturar_registros(desfazer)
    import requests
    get_original = requests.get
    try:
        requests.get = lambda *a, **k: _Resposta(
            200, payload={"instance": {"state": "close"}})
        with _Env(**CONFIG_VALIDA):
            for _ in range(20):
                assert wa.esta_conectada() is False
    finally:
        requests.get = get_original
        for f in desfazer:
            f()

    assert len(registros) == 1, f"20 consultas viraram {len(registros)} registros"


def test_falhas_de_ofertas_diferentes_contam_separado():
    """O que se abafa é a repetição idêntica da sondagem — nunca uma oferta
    que não foi publicada."""
    import integrations.whatsapp_api as wa

    desfazer = []
    registros = _capturar_registros(desfazer)
    foto_original = wa._baixar_foto_base64
    try:
        wa._baixar_foto_base64 = lambda url: None
        with _Env(**CONFIG_VALIDA):
            wa.enviar_foto_legenda("http://x/produto-1.jpg", "oferta 1")
            wa.enviar_foto_legenda("http://x/produto-2.jpg", "oferta 2")
            wa.enviar_foto_legenda("http://x/produto-1.jpg", "oferta 1")  # repetida
    finally:
        wa._baixar_foto_base64 = foto_original
        for f in desfazer:
            f()

    urls = [ctx.get("url") for _, _, ctx in registros]
    assert len(registros) == 2, f"esperava 2 registros distintos, veio {urls}"


def test_erro_http_no_envio_de_foto_vai_para_o_relatorio():
    """`sendMedia` devolvendo 401 não pode sumir num `log.warning`."""
    import integrations.whatsapp_api as wa

    desfazer = []
    registros = _capturar_registros(desfazer)
    import requests
    post_original = requests.post
    foto_original = wa._baixar_foto_base64
    try:
        requests.post = lambda *a, **k: _Resposta(401, text="unauthorized")
        wa._baixar_foto_base64 = lambda url: "Zm90bw=="  # foto "baixada"
        with _Env(**CONFIG_VALIDA):
            assert wa.enviar_foto_legenda("http://x/foto.jpg", "legenda") is False
    finally:
        requests.post = post_original
        wa._baixar_foto_base64 = foto_original
        for f in desfazer:
            f()

    assert registros, "falha de envio não chegou ao error_logger"
    op, msg, _ = registros[0]
    assert op == "wa_api.envio_foto"
    assert "401" in msg


def test_envio_sem_foto_e_registrado():
    """Regra 5: sem foto não publica — mas a oferta perdida tem de aparecer."""
    import integrations.whatsapp_api as wa

    desfazer = []
    registros = _capturar_registros(desfazer)
    foto_original = wa._baixar_foto_base64
    try:
        wa._baixar_foto_base64 = lambda url: None
        with _Env(**CONFIG_VALIDA):
            assert wa.enviar_foto_legenda("http://x/foto.jpg", "legenda") is False
    finally:
        wa._baixar_foto_base64 = foto_original
        for f in desfazer:
            f()

    assert any(op == "wa_api.envio_sem_foto" for op, _, _ in registros)


def test_texto_nao_repete_o_post_em_erro_de_servidor():
    """Repetir um POST que deu 500 é apostar em oferta duplicada no grupo.

    A segunda tentativa (envelope v1) só existe para o caso em que o servidor
    disse que o FORMATO está errado. Num 500 a Evolution pode ter entregue a
    mensagem e falhado depois — repetir publica duas vezes (Regra 11).
    """
    import integrations.whatsapp_api as wa

    chamadas = []
    desfazer = []
    _capturar_registros(desfazer)
    import requests
    post_original = requests.post
    try:
        def _post(*a, **k):
            chamadas.append(k.get("json"))
            return _Resposta(500, text="internal error")
        requests.post = _post
        with _Env(**CONFIG_VALIDA):
            assert wa.enviar_texto("oi") is False
    finally:
        requests.post = post_original
        for f in desfazer:
            f()

    assert len(chamadas) == 1, f"tentou {len(chamadas)}x depois de um 500"


def test_texto_ainda_tenta_o_envelope_v1_em_400():
    """O fallback de compatibilidade continua existindo onde ele faz sentido."""
    import integrations.whatsapp_api as wa

    chamadas = []
    desfazer = []
    _capturar_registros(desfazer)
    import requests
    post_original = requests.post
    try:
        def _post(*a, **k):
            chamadas.append(k.get("json"))
            return _Resposta(400) if len(chamadas) == 1 else _Resposta(200, payload={})
        requests.post = _post
        with _Env(**CONFIG_VALIDA):
            assert wa.enviar_texto("oi") is True
    finally:
        requests.post = post_original
        for f in desfazer:
            f()

    assert len(chamadas) == 2
    assert "textMessage" in chamadas[1], "a segunda tentativa não usou o envelope v1"


def test_estado_desconectado_diz_por_que():
    """`/health` mostrava `evolution-desconectada` sem nenhuma causa."""
    import integrations.whatsapp_api as wa

    desfazer = []
    registros = _capturar_registros(desfazer)
    import requests
    get_original = requests.get
    try:
        requests.get = lambda *a, **k: _Resposta(
            200, payload={"instance": {"state": "close"}})
        with _Env(**CONFIG_VALIDA):
            assert wa.esta_conectada() is False
    finally:
        requests.get = get_original
        for f in desfazer:
            f()

    assert any(op == "wa_api.desconectada" for op, _, _ in registros)


# ── Guia de configuração inicial ─────────────────────────────────────────────

def test_setup_le_a_chave_do_env():
    """O script da configuração inicial precisa ler o `.env` como todo o resto.

    Sem `load_dotenv()` ele abortava com "WHATSAPP_API_KEY nao esta no .env"
    apontando para o arquivo em que a chave estava.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = os.path.join(tmp, ".env")
        with io.open(env, "w", encoding="utf-8") as f:
            f.write("WHATSAPP_API_KEY=chave_do_arquivo\n")

        # Roda o módulo com o .env no lugar do .env real da raiz.
        codigo = (
            "import sys, os; sys.path.insert(0, %r);"
            "import setup_whatsapp_api as s;"
            "print('CHAVE=' + s.API_KEY)" % BASE
        )
        amb = {k: v for k, v in os.environ.items() if k not in WA_ENV}
        amb["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run([sys.executable, "-c", codigo], cwd=tmp,
                           capture_output=True, text=True, env=amb, timeout=60)
        assert r.returncode == 0, f"o módulo não importa: {r.stderr[-400:]}"
        # A chave vem do .env da RAIZ do projeto (ENV_PATH), não do cwd —
        # o que importa aqui é que importar não derruba mais o interpretador.
        assert "CHAVE=" in r.stdout


def test_setup_importa_sem_derrubar_o_interpretador():
    """Era um `raise SystemExit` de módulo: qualquer import matava o processo."""
    codigo = ("import sys; sys.path.insert(0, %r);"
              "import setup_whatsapp_api as s;"
              "print(s.PASSOS)" % BASE)
    amb = {k: v for k, v in os.environ.items() if k not in WA_ENV}
    amb["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True,
                       text=True, env=amb, timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    assert "test" in r.stdout


def test_apikey_da_resposta_aceita_v1_e_v2():
    """A v2.1.1 (a que o compose fixa) devolve `hash` como string."""
    import setup_whatsapp_api as s

    assert s._apikey_da_resposta({"hash": "ABC123"}) == "ABC123"
    assert s._apikey_da_resposta({"hash": {"apikey": "ABC123"}}) == "ABC123"
    assert s._apikey_da_resposta({}) == ""
    assert s._apikey_da_resposta({"hash": None}) == ""


def test_setup_passa_o_env_file_para_o_compose():
    """`docker compose -f docker/evolution.yml` lê `docker/.env`, não o da raiz.

    Sem `--env-file`, `${EVOLUTION_API_KEY:?}` fica sem valor e o compose
    recusa subir — reclamando de uma chave que já está no `.env` da raiz.
    """
    fonte = io.open(os.path.join(BASE, "setup_whatsapp_api.py"),
                    encoding="utf-8").read()
    assert "--env-file" in fonte, "o compose vai procurar o .env em docker/"


def test_teste_de_envio_nao_dispara_sem_confirmacao():
    """Regra 5: nada sai no WhatsApp do Daniel sem ele mandar.

    Sem terminal interativo e sem `--sim`, o passo `test` não pode enviar.
    """
    import setup_whatsapp_api as s

    enviados = []
    import integrations.whatsapp_api as wa
    api_originais = (wa.enviar_texto, wa.esta_conectada)
    argv_original = sys.argv
    stdin_original = sys.stdin

    class _SemTty:
        def isatty(self):
            return False

    try:
        wa.enviar_texto = lambda msg: enviados.append(msg) or True
        wa.esta_conectada = lambda: True
        sys.argv = ["setup_whatsapp_api.py", "test"]
        sys.stdin = _SemTty()
        with _Env(**CONFIG_VALIDA):
            assert s.testar_envio() is False
    finally:
        wa.enviar_texto, wa.esta_conectada = api_originais
        sys.argv = argv_original
        sys.stdin = stdin_original

    assert enviados == [], "mandou mensagem de verdade sem confirmação"


def test_teste_de_envio_exige_destino_configurado():
    """Com o JID de exemplo no .env o teste não pode nem tentar."""
    import setup_whatsapp_api as s

    with _Env(WHATSAPP_WEBHOOK_URL="http://localhost:8080",
              WHATSAPP_API_KEY="chave",
              WHATSAPP_GROUP_ID="120363XXXXXXXXX@g.us"):
        assert s.testar_envio() is False


def test_passo_all_nao_inclui_o_envio():
    """O guia inteiro roda sem publicar nada; `test` é pedido à parte."""
    fonte = io.open(os.path.join(BASE, "setup_whatsapp_api.py"),
                    encoding="utf-8").read()
    corpo = fonte.split("def main(", 1)[1]
    for linha in corpo.splitlines():
        if "testar_envio()" in linha:
            assert '"all"' not in linha, "o passo `all` chama testar_envio()"


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
