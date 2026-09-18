# -*- coding: utf-8 -*-
"""
A instalacao e a operacao pelo PowerShell continuam de pe?

Trava os quatro modos de falha que existiam de verdade nos scripts e que
nenhum teste pegava — todos silenciosos, todos do tipo "o script diz que deu
certo e nao deu":

  1. `.\\stop.ps1` dizia "Bot parado" e deixava tres processos vivos.
     O padrao era `*rastreador.py*`, e "rastreador_amazon.py" NAO contem
     "rastreador.py": o rastreador da Amazon, a campanha de ferramentas e a
     fila do WhatsApp sobreviviam ao stop e a Amazon seguia publicando.

  2. `docker compose -f docker/evolution.yml up -d` — o comando que o
     install.ps1 imprimia e que o setup_whatsapp_api.py executava — recusa
     subir mesmo com a EVOLUTION_API_KEY preenchida no .env da raiz. Com
     `-f docker/...`, o compose adota `docker/` como diretorio do projeto e
     procura o .env ALI. Sem `--env-file` nunca funcionou.

  3. `install.ps1` e `start.ps1` mandavam rodar `.\\logs.ps1`, que nunca
     existiu no repositorio.

  4. `install.ps1` gravava OK do Chromium do Playwright mesmo quando o
     download falhava, e o pip perdia a saida do erro.

Rodar:
    python tests/test_instalacao_powershell.py
    python -m pytest tests/test_instalacao_powershell.py -v
"""
import glob
import io
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ler(nome: str) -> str:
    """Le um .ps1 tratando o BOM (todos tem, e precisam ter)."""
    with io.open(os.path.join(BASE, nome), encoding="utf-8-sig") as f:
        return f.read()


# ── 1. stop.ps1 para tudo o que o status.ps1 monitora ─────────────────────

def test_stop_mata_todo_processo_que_o_status_monitora():
    """O bug que motivou este arquivo.

    A lista do stop.ps1 tem que cobrir cada processo que o status.ps1
    mostra. Se divergirem outra vez, `.\\stop.ps1` volta a mentir.
    """
    status = _ler("status.ps1")
    stop = _ler("stop.ps1")

    # Os padroes que o status.ps1 monitora, lidos da tabela dele.
    monitorados = re.findall(r'"(\*[a-z_]+\.py\*)"', status)
    assert len(monitorados) >= 5, f"nao achei a tabela do status.ps1: {monitorados}"

    faltando = [p for p in monitorados if p not in stop]
    assert not faltando, (
        "processo monitorado pelo status.ps1 que o stop.ps1 NAO mata — "
        "`.\\stop.ps1` diria 'Bot parado' com ele ainda publicando:\n  "
        + "\n  ".join(faltando))


def test_stop_mata_o_pai_antes_dos_filhos():
    """Regra 10: o startup.py e o pai e relanca os filhos. Matando um filho
    antes do pai, o pai o sobe de novo e o stop nao para nada."""
    stop = _ler("stop.ps1")
    pos_pai = stop.find('"*startup.py*"')
    pos_filho = stop.find('"*rastreador.py*"')
    assert pos_pai != -1 and pos_filho != -1, "padroes nao encontrados no stop.ps1"
    assert pos_pai < pos_filho, (
        "o stop.ps1 mata um filho antes do pai: o startup.py relanca o filho "
        "e o stop nao para nada (Regra 10)")


def test_stop_checa_execucao_em_andamento():
    """Regra 10: checar core.database.execucao_em_andamento() antes de
    desligar processo. Matar no meio de uma rodada deixa o claim do produto
    pendurado."""
    stop = _ler("stop.ps1")
    assert "execucao_em_andamento" in stop, (
        "stop.ps1 nao checa execucao_em_andamento() — mataria uma rodada de "
        "publicacao pela metade (Regra 10)")
    assert "-Forcar" in stop or "Forcar" in stop, (
        "sem uma saida explicita, uma execucao travada impediria parar o bot")


# ── 2. docker compose enxerga o .env da raiz ──────────────────────────────

def test_todo_docker_compose_do_evolution_passa_env_file():
    """Sem `--env-file`, o compose procura o .env dentro de docker/ e recusa
    subir com "required variable EVOLUTION_API_KEY is missing a value" — com
    a chave preenchida na raiz. Vale para quem EXECUTA e para quem DOCUMENTA
    o comando: a instrucao errada custa o mesmo tempo perdido."""
    alvos = ["install.ps1", "setup_whatsapp_api.py", "INSTALL.md",
             "integrations/whatsapp_api.py"]
    problemas = []
    for alvo in alvos:
        caminho = os.path.join(BASE, alvo)
        if not os.path.exists(caminho):
            continue
        with io.open(caminho, encoding="utf-8-sig") as f:
            texto = f.read()
        for linha in texto.splitlines():
            if "evolution.yml" not in linha:
                continue
            if "compose" not in linha and '"-f"' not in linha:
                continue          # so menciona o arquivo, nao monta comando
            if "up" not in linha and "stop" not in linha and "logs" not in linha:
                continue          # nao e uma invocacao
            if "--env-file" not in linha and "env_file" not in texto:
                problemas.append(f"{alvo}: {linha.strip()[:100]}")
    assert not problemas, (
        "invocacao do docker compose sem --env-file (o compose procuraria o "
        ".env em docker/, nao na raiz):\n  " + "\n  ".join(problemas))


# ── 3. Todo script citado existe ──────────────────────────────────────────

def test_todo_script_citado_existe():
    """`install.ps1` e `start.ps1` mandavam rodar `.\\logs.ps1` e o arquivo
    nao existia: quem seguia a instrucao recebia "O termo '.\\logs.ps1' nao e
    reconhecido". Uma instrucao que nao roda e pior que nenhuma."""
    referencia = re.compile(r'\.\\([A-Za-z0-9_\-]+\.ps1)')
    faltando = []
    for padrao in ("*.ps1", "*.bat"):
        for caminho in sorted(glob.glob(os.path.join(BASE, padrao))):
            with io.open(caminho, encoding="utf-8-sig", errors="replace") as f:
                texto = f.read()
            for alvo in sorted(set(referencia.findall(texto))):
                if not os.path.exists(os.path.join(BASE, alvo)):
                    faltando.append(f"{os.path.basename(caminho)} cita {alvo}")
    assert not faltando, (
        "script manda rodar um .ps1 que nao existe:\n  " + "\n  ".join(faltando))


# ── 4. install.ps1: honestidade e verificacao final ───────────────────────

def test_install_termina_verificando_com_healthcheck():
    install = _ler("install.ps1")
    assert "core.healthcheck" in install, (
        "install.ps1 nao faz a verificacao final — terminaria anunciando "
        "sucesso sem olhar se a instalacao ficou de pe")


def test_install_nao_engole_a_saida_de_erro():
    """`2>&1 | Out-Null` no playwright, com um "OK: Chromium instalado" fixo
    logo depois, anunciava sucesso mesmo quando o download falhava; e o
    `--quiet` do pip deixava a falha como "ERRO no pip install" sem dizer
    qual pacote quebrou.

    Olha as LINHAS que invocam os comandos, nao o arquivo inteiro: um
    `| Out-Null` em `New-Item` e legitimo (descarta o objeto criado, nao uma
    mensagem de erro)."""
    install = _ler("install.ps1")

    engolidas = [l.strip() for l in install.splitlines()
                 if ("playwright" in l or "pip" in l) and "Out-Null" in l
                 and not l.strip().startswith("#")]   # comentario explica, nao executa
    assert not engolidas, (
        "invocacao com a saida descartada — a falha viraria 'OK':\n  "
        + "\n  ".join(engolidas))

    assert "$r.Saida" in install, (
        "install.ps1 nao mostra a saida do comando que falhou")
    # O caso do Chromium: falhar nao pode imprimir sucesso.
    depois_playwright = install.split('"playwright"')[-1][:600]
    assert "Aviso" in depois_playwright, (
        "a falha do Chromium do Playwright nao gera aviso")


def test_install_grava_env_sem_bom():
    """Set-Content -Encoding UTF8 do PowerShell 5.1 grava BOM, e o
    python-dotenv leria a PRIMEIRA variavel do .env como
    "\\ufeffTOKEN_TELEGRAM": a chave estaria la e o bot nao a acharia."""
    install = _ler("install.ps1")
    if "EVOLUTION_API_KEY=" not in install:
        return
    assert "UTF8Encoding($false)" in install, (
        "install.ps1 grava o .env sem garantir ausencia de BOM")
    gravacoes = re.findall(r'(Set-Content|Out-File)[^\n]*\$envPath', install)
    assert not gravacoes, (
        f"gravacao do .env por cmdlet que pode por BOM: {gravacoes}")


def test_install_exige_python_minimo_e_detecta_o_atalho_da_store():
    """O Windows 10/11 traz um python.exe em WindowsApps que abre a
    Microsoft Store e sai com 9009. Get-Command acha esse atalho, entao
    "existe python" nao e "existe Python" — e o install.ps1 antigo dava OK
    aqui e so quebrava la no pip, sem dizer por que."""
    install = _ler("install.ps1")
    assert "WindowsApps" in install, (
        "install.ps1 nao detecta o atalho da Microsoft Store")
    assert "PY_MINIMO" in install, "install.ps1 nao checa a versao do Python"


# ── 5. start.ps1 confirma que o bot continuou de pe ───────────────────────

def test_start_confirma_que_o_processo_sobreviveu():
    """"Chamar Popen nao e o mesmo que ter subido" (Regra 15): com .env
    invalido o startup.py sai em ~1s. O start.ps1 antigo dormia 6s e
    imprimia "Bot iniciado" em verde de qualquer jeito."""
    start = _ler("start.ps1")
    assert "startup.py" in start, "start.ps1 nao sobe o processo pai"
    assert "rastreador.py --loop" not in start and "rastreador.py --random" not in start, (
        "start.ps1 subindo um filho direto — Regra 10 manda subir o pai")
    # A prova de que ele confere depois de subir, e nao so dorme.
    assert re.search(r"Get-CimInstance[^\n]*\n?[^\n]*startup\.py", start) or \
        start.count("startup.py") >= 2, (
        "start.ps1 nao reconfere se o startup.py continuou vivo")
    assert "Get-Content" in start, (
        "start.ps1 nao mostra o log quando o bot morre na subida")


# ── 6. A verificacao da instalacao funciona de verdade ────────────────────

def test_healthcheck_tem_modo_de_linha_de_comando():
    caminho = os.path.join(BASE, "core", "healthcheck.py")
    with io.open(caminho, encoding="utf-8") as f:
        texto = f.read()
    assert '__name__ == "__main__"' in texto, (
        "core/healthcheck.py sem entrada de linha de comando: o install.ps1 "
        "nao teria como fazer a verificacao final")
    assert "def verificar_instalacao" in texto
    assert "def coletar" in texto, (
        "o /health e a verificacao precisam da MESMA fonte de estado")


def test_verificacao_reprova_env_com_valor_de_exemplo():
    """O install.ps1 cria o .env copiando o .env.example. "A variavel
    existe" nao e "a variavel foi preenchida" — e reprovar isso e todo o
    sentido da etapa 8/8."""
    sys.path.insert(0, BASE)
    from core import healthcheck as hc

    exemplo = hc._valores_do_exemplo()
    assert "TOKEN_TELEGRAM" in exemplo, ".env.example sem TOKEN_TELEGRAM"

    anterior = os.environ.get("TOKEN_TELEGRAM")
    try:
        os.environ["TOKEN_TELEGRAM"] = exemplo["TOKEN_TELEGRAM"]
        ok, motivo = hc._preenchida("TOKEN_TELEGRAM", exemplo)
        assert ok is False, "valor de exemplo passando como configurado"
        assert "exemplo" in motivo

        os.environ["TOKEN_TELEGRAM"] = ""
        ok, _ = hc._preenchida("TOKEN_TELEGRAM", exemplo)
        assert ok is False, "variavel vazia passando como configurada"

        os.environ["TOKEN_TELEGRAM"] = "123456:valor_real_qualquer"
        ok, _ = hc._preenchida("TOKEN_TELEGRAM", exemplo)
        assert ok is True, "valor real sendo reprovado"
    finally:
        if anterior is None:
            os.environ.pop("TOKEN_TELEGRAM", None)
        else:
            os.environ["TOKEN_TELEGRAM"] = anterior


def test_coletar_aguenta_coletor_que_estoura():
    """Um coletor levantando excecao derrubava o handler inteiro e o /health
    devolvia 500 sem corpo — que o status.ps1 le como "healthcheck OFF", ou
    seja, bot inteiro dado como morto por causa de um componente opcional."""
    sys.path.insert(0, BASE)
    from core import healthcheck as hc

    original = hc._COLETORES
    try:
        def explode():
            raise RuntimeError("componente quebrado")

        hc._COLETORES = (("chrome", explode), ("telegram", hc._status_telegram),
                         ("rastreador", hc._status_rastreador))
        carga = hc.coletar()
        assert carga["chrome"]["ok"] is False
        assert "componente quebrado" in carga["chrome"]["erro"]
        assert "ok" in carga and "criticos_com_falha" in carga
    finally:
        hc._COLETORES = original


# ── 7. INSTALL.md documenta o caminho real ────────────────────────────────

def test_install_md_cobre_os_scripts_de_operacao():
    caminho = os.path.join(BASE, "INSTALL.md")
    assert os.path.exists(caminho), "INSTALL.md nao existe"
    with io.open(caminho, encoding="utf-8") as f:
        doc = f.read()
    for script in ("install.ps1", "start.ps1", "status.ps1", "logs.ps1", "stop.ps1"):
        assert script in doc, f"INSTALL.md nao menciona {script}"
    assert "core.healthcheck" in doc, "INSTALL.md nao ensina a conferir a instalacao"


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
