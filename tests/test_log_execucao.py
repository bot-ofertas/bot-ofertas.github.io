# -*- coding: utf-8 -*-
"""
Testes do log de execução na Área de Trabalho (core/execucao_log.py).

O pedido que estes testes protegem: toda execução do bot grava, num arquivo
de texto dentro da pasta "problemas de execução" da Área de Trabalho, se
houve erro e em que ponto. As três armadilhas cobertas aqui são as que
tornariam o arquivo inútil sem ninguém perceber:

  1. dizer "SEM ERROS" numa execução que quebrou;
  2. perder a execução que morreu no meio (PC desligado) — que é exatamente
     quando o Daniel vai olhar o arquivo;
  3. vazar token no texto (o repositório é público e o arquivo vai anexado
     no diagnóstico).

Rodar:
    python tests/test_log_execucao.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import execucao_log

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _PastaDeTeste:
    """Aponta o log para uma pasta temporária e zera o estado do módulo."""

    def __enter__(self) -> str:
        self._tmp = tempfile.TemporaryDirectory()
        self._antigo = os.environ.get("BOT_PASTA_PROBLEMAS")
        os.environ["BOT_PASTA_PROBLEMAS"] = os.path.join(
            self._tmp.name, "problemas de execução")
        execucao_log._pilha.clear()
        execucao_log.pasta_problemas(recarregar=True)
        return execucao_log.pasta_problemas()

    def __exit__(self, *_):
        execucao_log._pilha.clear()
        if self._antigo is None:
            os.environ.pop("BOT_PASTA_PROBLEMAS", None)
        else:
            os.environ["BOT_PASTA_PROBLEMAS"] = self._antigo
        execucao_log.pasta_problemas(recarregar=True)
        self._tmp.cleanup()


def _ler_log() -> str:
    with open(execucao_log.caminho_log(), encoding="utf-8") as f:
        return f.read()


# ── Pasta e arquivos ─────────────────────────────────────────────────────────

def test_pasta_e_criada_com_leia_me_e_subpasta():
    with _PastaDeTeste() as pasta:
        assert os.path.isdir(pasta)
        assert os.path.basename(pasta) == "problemas de execução"
        assert os.path.isdir(os.path.join(pasta, "em andamento"))
        assert os.path.isfile(os.path.join(pasta, "LEIA-ME.txt"))


def test_os_tres_arquivos_ficam_na_mesma_pasta():
    """Um lugar só: o log de execução, os erros detalhados e o relatório.
    Antes eram dois .txt soltos na mesa com nomes parecidos."""
    with _PastaDeTeste() as pasta:
        for caminho in (execucao_log.caminho_log(),
                        execucao_log.caminho_erros_detalhados(),
                        execucao_log.caminho_relatorio()):
            assert os.path.dirname(caminho) == pasta


def test_ponteiro_para_o_powershell():
    """start.ps1/status.ps1 leem este arquivo em vez de repetir em PowerShell
    a busca pela Área de Trabalho (que muda com OneDrive)."""
    with _PastaDeTeste() as pasta:
        with open(execucao_log.PONTEIRO, encoding="utf-8") as f:
            linhas = [l.strip() for l in f if l.strip()]
        assert linhas[0] == pasta
        assert linhas[1] == execucao_log.caminho_log()


def test_migra_os_arquivos_soltos_da_mesa():
    with tempfile.TemporaryDirectory() as tmp:
        antigo = os.environ.get("BOT_PASTA_PROBLEMAS")
        solto = os.path.join(tmp, "Problemas de execução para corrigir.txt")
        with open(solto, "w", encoding="utf-8") as f:
            f.write("erro antigo\n")
        os.environ["BOT_PASTA_PROBLEMAS"] = os.path.join(tmp, "problemas de execução")
        try:
            execucao_log.pasta_problemas(recarregar=True)
            assert not os.path.exists(solto), "arquivo antigo ficou solto na mesa"
            with open(execucao_log.caminho_erros_detalhados(), encoding="utf-8") as f:
                assert "erro antigo" in f.read()
        finally:
            if antigo is None:
                os.environ.pop("BOT_PASTA_PROBLEMAS", None)
            else:
                os.environ["BOT_PASTA_PROBLEMAS"] = antigo
            execucao_log.pasta_problemas(recarregar=True)


# ── Veredito do bloco ────────────────────────────────────────────────────────

def test_execucao_limpa_diz_sem_erros():
    with _PastaDeTeste():
        with execucao_log.abrir_execucao("rodada de teste") as ex:
            ex.etapa("banco de dados pronto")
            ex.definir_resumo("2 publicado(s)")
        texto = _ler_log()
        assert "EXECUÇÃO   : rodada de teste" in texto
        assert "RESULTADO  : SEM ERROS" in texto
        assert "banco de dados pronto" in texto
        assert "Resumo     : 2 publicado(s)" in texto


def test_execucao_com_erro_diz_o_ponto_da_falha():
    with _PastaDeTeste():
        def _quebra():
            raise TimeoutError("WhatsApp Desktop não respondeu")

        with execucao_log.abrir_execucao("rodada com falha") as ex:
            ex.etapa("banco de dados pronto")
            try:
                _quebra()
            except TimeoutError as e:
                ex.erro("envio para o WhatsApp", exc=e)
        texto = _ler_log()
        assert 'RESULTADO  : ERRO — falhou em "envio para o WhatsApp"' in texto
        assert "TimeoutError: WhatsApp Desktop não respondeu" in texto
        # "em que ponto": arquivo, função e linha de onde a exceção estourou,
        # não de onde ela foi capturada.
        assert "onde: test_log_execucao.py → _quebra(), linha" in texto


def test_excecao_que_escapa_fecha_o_bloco_e_sobe():
    with _PastaDeTeste():
        try:
            with execucao_log.abrir_execucao("rodada abortada"):
                raise RuntimeError("estourou no meio")
        except RuntimeError:
            pass
        else:
            raise AssertionError("a exceção precisa continuar subindo")
        texto = _ler_log()
        assert "RESULTADO  : ERRO" in texto
        assert "RuntimeError: estourou no meio" in texto


def test_erro_repetido_na_hora_nao_duplica_linha():
    """A mesma falha chega por dois caminhos (chamador + db.registrar_erro)."""
    with _PastaDeTeste():
        with execucao_log.abrir_execucao("rodada") as ex:
            ex.erro("rodada do rastreador", mensagem="Timed out")
            ex.erro("rodada do rastreador", mensagem="Timed out")
        assert _ler_log().count("Timed out") == 1


# ── Execução que morre no meio ───────────────────────────────────────────────

def test_execucao_interrompida_e_recolhida_pela_seguinte():
    with _PastaDeTeste() as pasta:
        orfao = os.path.join(pasta, "em andamento", "rastreador.py-999999.txt")
        with open(orfao, "w", encoding="utf-8") as f:
            f.write("=" * 74 + "\n")
            f.write("EXECUÇÃO   : rodada de ontem\n")
            f.write("-" * 74 + "\n")
            f.write("  23:59:00  [ok]   varredura das categorias iniciada\n")

        with execucao_log.abrir_execucao("rodada de hoje"):
            pass

        texto = _ler_log()
        assert "rodada de ontem" in texto
        assert "RESULTADO  : INTERROMPIDA" in texto
        assert not os.path.exists(orfao), "o órfão devia sair de em andamento/"
        # e a execução nova continua com o veredito dela
        assert "RESULTADO  : SEM ERROS" in texto


def test_execucao_em_andamento_fica_visivel_enquanto_roda():
    with _PastaDeTeste() as pasta:
        pasta_andamento = os.path.join(pasta, "em andamento")
        ex = execucao_log.abrir_execucao("rodada em curso")
        ex.etapa("varredura das categorias iniciada")
        assert os.listdir(pasta_andamento), "nada em em andamento/ durante a execução"
        ex.fechar()
        assert not os.listdir(pasta_andamento), "sobrou arquivo depois de fechar"


def test_processo_vivo_reconhece_este_processo():
    """Se esta checagem responder errado, uma execução viva é recolhida como
    interrompida — ou um órfão nunca é recolhido."""
    assert execucao_log._processo_vivo(os.getpid()) is True
    assert execucao_log._processo_vivo(999999) in (False, None)


# ── Segredos ─────────────────────────────────────────────────────────────────

def test_bloco_nao_vaza_token():
    """O arquivo é anexado no coletar_diagnostico.ps1 e o repo é público."""
    with _PastaDeTeste():
        falso = "123456789:AA" + "b" * 33
        with execucao_log.abrir_execucao("rodada", comando=f"python bot.py --token {falso}") as ex:
            ex.erro("telegram", mensagem=f"The token `{falso}` was rejected")
        texto = _ler_log()
        assert falso not in texto
        assert "***REDIGIDO***" in texto


# ── Integração com o error_logger ────────────────────────────────────────────

def test_log_erro_marca_o_ponto_de_falha_no_bloco():
    """`log_erro()` é o caminho de quem captura a exceção e segue (Regra 6).
    Sem isto, o bloco diria SEM ERROS numa rodada em que o envio falhou."""
    with _PastaDeTeste():
        from core.error_logger import log_erro  # import tardio: usa a pasta do teste

        with execucao_log.abrir_execucao("rodada"):
            try:
                raise ValueError("grupo não encontrado")
            except ValueError as e:
                log_erro("whatsapp.envio", e, {"grupo": "Bot-Ofertas"})
        texto = _ler_log()
        assert 'RESULTADO  : ERRO — falhou em "whatsapp.envio"' in texto
        assert os.path.isfile(execucao_log.caminho_erros_detalhados())


def test_registrar_evento_conta_falha_sem_excecao():
    with _PastaDeTeste():
        from core.error_logger import registrar_evento  # noqa: PLC0415

        with execucao_log.abrir_execucao("rodada"):
            registrar_evento("whatsapp.envio_falhou",
                             "nenhum caminho de envio funcionou — Evolution API: não configurada")
        texto = _ler_log()
        assert "RESULTADO  : ERRO" in texto
        assert "Evolution API: não configurada" in texto


def test_whatsapp_sender_registra_o_motivo_de_cada_tentativa():
    with _PastaDeTeste():
        from integrations.whatsapp_sender import _registrar_envio_falhou  # noqa: PLC0415

        with execucao_log.abrir_execucao("rodada"):
            _registrar_envio_falhou(
                ["Evolution API: não configurada",
                 "WhatsApp Desktop: o aplicativo não está rodando (abra e deixe logado)"],
                {"id": "MLB123", "titulo": "Smartphone"}, "Bot-Ofertas")
        texto = _ler_log()
        assert "RESULTADO  : ERRO" in texto
        assert "WhatsApp Desktop: o aplicativo não está rodando" in texto


# ── Healthcheck ──────────────────────────────────────────────────────────────

def test_healthcheck_isola_componente_que_estoura():
    """Um import quebrado num componente derrubava /health inteiro com 500, e
    o status.ps1 só dizia "Healthcheck OFF" — sem dizer o que caiu."""
    from core import healthcheck  # noqa: PLC0415

    def _estoura():
        raise ImportError("psutil não instalado")

    original = healthcheck._COMPONENTES
    healthcheck._COMPONENTES = {
        "telegram": lambda: {"ok": True},
        "rastreador": lambda: {"ok": True},
        "quebrado": _estoura,
    }
    try:
        status = healthcheck.montar_status()
    finally:
        healthcheck._COMPONENTES = original
    assert status["ok"] is True
    assert status["quebrado"]["ok"] is False
    assert "psutil não instalado" in status["quebrado"]["erro"]


def test_healthcheck_roda_avulso():
    """A rotina diária do projeto chama `python -m core.healthcheck`."""
    from core import healthcheck  # noqa: PLC0415

    assert callable(healthcheck.verificar_uma_vez)
    fonte = open(os.path.join(BASE, "core", "healthcheck.py"), encoding="utf-8").read()
    assert 'if __name__ == "__main__":' in fonte


# ── Scripts de operação ──────────────────────────────────────────────────────

def test_start_e_status_apontam_para_o_log():
    for nome in ("start.ps1", "status.ps1"):
        texto = open(os.path.join(BASE, nome), encoding="utf-8-sig").read()
        assert "caminho_log_execucao.txt" in texto, f"{nome} não lê o ponteiro"
        assert "problemas de execução" in texto, f"{nome} não cita a pasta"
    status = open(os.path.join(BASE, "status.ps1"), encoding="utf-8-sig").read()
    assert "log de execução.txt" in status, "status.ps1 não mostra o log de execução"


def test_scripts_de_operacao_mantem_o_bom():
    """PowerShell 5.1 lê .ps1 sem BOM como cp1252 e o acento da pasta viraria
    lixo no caminho — ou terminador de string."""
    for nome in ("start.ps1", "status.ps1", "coletar_diagnostico.ps1"):
        with open(os.path.join(BASE, nome), "rb") as f:
            assert f.read(3) == b"\xef\xbb\xbf", f"{nome} perdeu o BOM UTF-8"


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
