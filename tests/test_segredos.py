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
