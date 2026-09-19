# -*- coding: utf-8 -*-
"""
Testes da redação de segredos em log.

Existem por causa de um vazamento real, achado em 2026-09-07: este
repositório é PÚBLICO e tinha dois arquivos de log versionados com o token
do bot do Telegram em texto puro — `monitor.log` (1 ocorrência) e
`rastreador.log` (20 ocorrências, dois tokens distintos).

Os dois caminhos que colocaram o token lá são diferentes, e é por isso que
o teste cobre os dois separadamente:

  - `monitor.py` gravava `str(e)`, e a exceção da python-telegram-bot é
    literalmente "The token `<token>` was rejected by the server";
  - o logger do `httpx` registra a URL de cada requisição, e no Telegram o
    token faz parte da URL — chegando ao handler como ARGUMENTO de
    formatação, não dentro da mensagem.

O último teste é a trava que faltava: falha se QUALQUER arquivo versionado
voltar a conter um segredo. `.gitignore` não serve para isso —
`rastreador.log` estava listado nele e mesmo assim versionado, porque
gitignore não vale para arquivo que já foi commitado uma vez.

Rodar:
    python tests/test_segredos.py
    python -m pytest tests/test_segredos.py -v
"""
import io
import logging
import os
import pathlib
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core.segredos import MASCARA, FiltroDeSegredos, redigir  # noqa: E402

# Tokens de mentira, montados em PEDAÇOS de proposito.
#
# Eles precisam ter o formato real, senao nao exercitam o padrao que a
# redacao usa. Mas escritos inteiros no arquivo eles casam com a varredura
# de `test_nenhum_arquivo_versionado_contem_segredo` — que roda sobre TODO
# arquivo versionado, este inclusive — e o teste acusa a si mesmo (foi o que
# quebrou o CI em 2026-09-07: local passou porque rodei antes do `git add`,
# e `git ls-files` ainda nao via este arquivo).
#
# A saida NAO e excluir este arquivo da varredura: isso abriria justamente
# o ponto cego que ela existe para fechar — qualquer segredo de verdade
# colado aqui passaria batido. Montando em tempo de execucao, o valor tem o
# formato certo para os testes e nenhum literal do arquivo casa com o padrao.
_ID_FALSO = "1" + "234567890"
TOKEN_FALSO = _ID_FALSO + ":" + "AA" + "EJ1pb" + "TESTE" * 6
TOKEN_FALSO_2 = "9" + "876543210" + ":" + "AA" + "Gy43Qz" + "TESTE" * 6


def _logger_com_filtro():
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.addFilter(FiltroDeSegredos())
    log = logging.getLogger("teste_segredos_%d" % id(buf))
    log.handlers = [h]
    log.setLevel(logging.INFO)
    log.propagate = False
    return log, buf


def test_redige_token_que_esta_no_ambiente():
    os.environ["TOKEN_TELEGRAM"] = TOKEN_FALSO
    try:
        saida = redigir("falhou com o token " + TOKEN_FALSO)
        assert TOKEN_FALSO not in saida
        assert MASCARA in saida
    finally:
        os.environ.pop("TOKEN_TELEGRAM", None)


def test_redige_token_que_nao_esta_no_ambiente():
    """O token vazado em monitor.log era um token VELHO, já rejeitado — não
    estava mais no .env. Depender só do ambiente não teria pego ele."""
    os.environ.pop("TOKEN_TELEGRAM", None)
    assert TOKEN_FALSO_2 not in redigir("The token `%s` was rejected" % TOKEN_FALSO_2)


def test_filtro_pega_token_vindo_como_argumento():
    """O caso do httpx: `log.info("POST %s", url)`. O token não está em
    `record.msg`, está em `record.args` — filtrar só a mensagem deixaria
    passar exatamente as 20 linhas de rastreador.log."""
    log, buf = _logger_com_filtro()
    log.info('HTTP Request: POST %s "%s"',
             "https://api.telegram.org/bot%s/sendPhoto" % TOKEN_FALSO, "HTTP/1.1 200 OK")
    assert TOKEN_FALSO not in buf.getvalue()


def test_filtro_nao_estraga_log_normal():
    """Redigir demais também é defeito: um valor curto de ambiente
    (PAPEL=nuvem) não pode virar máscara em toda linha que o cite."""
    os.environ["PAPEL_TOKEN_CURTO"] = "nuvem"
    try:
        log, buf = _logger_com_filtro()
        log.info("PAPEL=nuvem, publicando 4 ofertas")
        assert "PAPEL=nuvem, publicando 4 ofertas" in buf.getvalue()
    finally:
        os.environ.pop("PAPEL_TOKEN_CURTO", None)


def test_redigir_aguenta_entrada_estranha():
    assert redigir("") == ""
    assert redigir(None) is None


def test_error_logger_aplica_filtro_nos_handlers():
    """O filtro tem que estar nos HANDLERS. Preso ao logger raiz ele não é
    consultado para registros que vêm propagados de outro logger — que é
    justamente o caso do httpx."""
    texto = open(os.path.join(BASE, "core", "error_logger.py"), encoding="utf-8").read()
    assert "ch.addFilter(filtro)" in texto
    assert "fh.addFilter(filtro)" in texto
    assert "redigir(str(exc))" in texto, "mensagem do errors.jsonl (e do n8n) sem redação"


def test_monitor_redige_antes_de_gravar():
    """monitor.py não passa por setup_logging — precisa do seu próprio."""
    texto = open(os.path.join(BASE, "monitor.py"), encoding="utf-8").read()
    assert "redigir(str(e))" in texto
    assert "FiltroDeSegredos" in texto


_PADROES_PROIBIDOS = (
    ("token do Telegram", re.compile(rb"\d{8,12}:AA[\w-]{30,}")),
    ("chave da Anthropic", re.compile(rb"sk-ant-api\d{2}-[\w-]{20,}")),
    ("token do Mercado Livre", re.compile(rb"APP_USR-[\w-]{20,}")),
)


def test_nenhum_arquivo_versionado_contem_segredo():
    """A trava. Repositório público: um segredo commitado é um segredo
    publicado, e o histórico do git não esquece."""
    saida = subprocess.run(["git", "ls-files", "-z"], cwd=BASE,
                           capture_output=True, check=True).stdout
    achados = []
    for nome in saida.split(b"\0"):
        if not nome:
            continue
        caminho = os.path.join(BASE, nome.decode())
        if not os.path.isfile(caminho) or os.path.getsize(caminho) > 5_000_000:
            continue
        with open(caminho, "rb") as f:
            conteudo = f.read()
        for rotulo, padrao in _PADROES_PROIBIDOS:
            if padrao.search(conteudo):
                achados.append("%s: %s" % (nome.decode(), rotulo))
    assert not achados, "segredo em arquivo versionado -> " + "; ".join(achados)


def test_logs_nao_voltam_a_ser_versionados():
    saida = subprocess.run(["git", "ls-files"], cwd=BASE,
                           capture_output=True, text=True, check=True).stdout.split()
    for proibido in ("monitor.log", "rastreador.log"):
        assert proibido not in saida, "%s voltou para o versionamento" % proibido


# ---------------------------------------------------------------------------
# empacotar_projeto.ps1 empacota a pasta inteira para analise fora do PC.
# A redacao dele e a unica coisa entre o codigo do Daniel e um anexo publico,
# entao ela precisa valer nos DOIS sentidos: apagar segredo de verdade e nao
# estragar codigo legitimo. Uma regra larga demais transformaria
# `API_KEY = os.getenv("API_KEY", "")` em `API_KEY = ***REDIGIDO***` e o dump
# perderia justamente o que ha para analisar.
# ---------------------------------------------------------------------------

_SCRIPT_EMPACOTA = pathlib.Path(BASE) / "empacotar_projeto.ps1"


def _regras_de_redacao():
    """Le as regras direto do .ps1 — testar uma copia nao prova nada."""
    src = _SCRIPT_EMPACOTA.read_text(encoding="utf-8")
    regras = re.findall(r"-replace '(.+?)', '(.+?)'", src)
    assert regras, "nenhuma regra -replace encontrada em empacotar_projeto.ps1"
    return regras


def _redigir_como_o_script(texto):
    for padrao, troca in _regras_de_redacao():
        padrao = padrao.replace("(?im)", "")
        texto = re.sub(padrao, troca.replace("$1", r"\1"), texto,
                       flags=re.IGNORECASE | re.MULTILINE)
    return texto


def test_empacotador_apaga_segredo_de_verdade():
    # montados em pedacos: um segredo literal aqui seria pego pela varredura
    # de test_nenhum_arquivo_versionado_contem_segredo, e com razao.
    casos = {
        "telegram": "api.telegram.org/bot" + "8939890814" + ":" + "AA" + "X" * 33,
        "anthropic": "sk-ant-" + "api03-" + "y" * 40,
        "mercadolivre": "APP_" + "USR-" + "1234567890123456-081512-abc",
        "github": "ghp_" + "Z" * 36,
        "env": "N8N_TOKEN=abcdef123456789xyz",
        "senha": "SENHA: umaSenhaBemLonga123",
    }
    for nome, bruto in casos.items():
        assert "REDIGIDO" in _redigir_como_o_script(bruto), \
            "empacotador deixaria passar segredo (%s): %s" % (nome, bruto[:30])


def test_empacotador_nao_estraga_codigo_legitimo():
    intactos = [
        'ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")',
        '_AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "")',
        '_TOOL_ID = os.getenv("ML_AFFILIATE_TOOL_ID") or "47114387"',
        "N8N_TOKEN=",
        "TOKEN_TELEGRAM=seu-token-aqui",
        "# TOKEN_TELEGRAM vem do .env, nunca do codigo",
    ]
    for linha in intactos:
        assert _redigir_como_o_script(linha) == linha, \
            "empacotador mutilou codigo legitimo: %s" % linha


def test_empacotador_nunca_inclui_o_env():
    """O .env so pode sair como NOMES de chave — nunca o arquivo."""
    src = _SCRIPT_EMPACOTA.read_text(encoding="utf-8")
    assert '$f.Name -eq ".env"' in src, "falta a exclusao explicita do .env"
    for proibido in ("ml_profile", "node_modules", "__pycache__"):
        assert proibido in src, "empacotador nao exclui %s" % proibido


def test_empacotador_e_lido_como_ascii_pelo_powershell():
    """PowerShell 5.1 le .ps1 sem BOM como cp1252. Um traco longo dentro de
    string vira terminador de string e o script nao roda. Dois cintos: BOM
    presente E corpo 100%% ASCII."""
    bruto = _SCRIPT_EMPACOTA.read_bytes()
    assert bruto[:3] == b"\xef\xbb\xbf", "empacotar_projeto.ps1 perdeu o BOM UTF-8"
    fora = [b for b in bruto[3:] if b > 127]
    assert not fora, "%d byte(s) nao-ASCII no corpo do .ps1" % len(fora)

def test_verificar_tudo_nunca_imprime_valor_de_segredo():
    """verificar_tudo.py LE o .env para dizer o que esta configurado. A saida
    dele e feita para ser colada numa conversa, entao nunca pode carregar o
    valor — so o nome da chave e o tamanho. Regra 10, e o vazamento de
    2026-09-07, que comecou exatamente assim: um log colado.

    A checagem e por AST, nao por regex: procurar a palavra "token" no texto
    acusa ate a mensagem "401 - token invalido", que nao vaza nada. O que
    importa e se a VARIAVEL que guarda o segredo chega a uma funcao de
    impressao.
    """
    import ast as _ast  # noqa: PLC0415

    alvo = pathlib.Path(BASE) / "verificar_tudo.py"
    arvore = _ast.parse(alvo.read_text(encoding="utf-8"))

    IMPRIME = {"ok", "falha", "aviso", "pulado", "print"}
    PERIGOSAS = {"v", "token"}          # onde o valor do segredo mora
    vazamentos = []

    for no in _ast.walk(arvore):
        if not (isinstance(no, _ast.Call) and isinstance(no.func, _ast.Name)
                and no.func.id in IMPRIME):
            continue
        for arg in no.args:
            # valor passado cru: ok("X", token)
            if isinstance(arg, _ast.Name) and arg.id in PERIGOSAS:
                vazamentos.append(f"linha {no.lineno}: {arg.id} passado cru")
            # interpolado numa f-string: f"...{token}..." — mas len(token) e ok
            if isinstance(arg, _ast.JoinedStr):
                for pedaco in _ast.walk(arg):
                    if isinstance(pedaco, _ast.FormattedValue):
                        for n2 in _ast.walk(pedaco.value):
                            if isinstance(n2, _ast.Name) and n2.id in PERIGOSAS:
                                envolto = any(
                                    isinstance(c, _ast.Call)
                                    and isinstance(c.func, _ast.Name)
                                    and c.func.id == "len"
                                    for c in _ast.walk(pedaco.value))
                                if not envolto:
                                    vazamentos.append(
                                        f"linha {no.lineno}: {n2.id} interpolado sem len()")

    assert not vazamentos, "verificar_tudo.py imprimiria segredo -> " + "; ".join(vazamentos)

    src = alvo.read_text(encoding="utf-8")
    assert "os.getenv" in src, "o teste ficou desatualizado — o script nao le mais o .env"
    assert 'f"{len(v)} caracteres"' in src, "sumiu o formato que mostra so o TAMANHO da chave"



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
