# -*- coding: utf-8 -*-
"""
Testes do bot rodando em servidor (DigitalOcean e afins).

Cada teste aqui existe por causa de um defeito concreto encontrado no
material de deploy antes desta mudança — não são testes de "será que
funciona", são travas para o que já estava errado:

  - três publicadores (PC, GitHub Actions, servidor) capazes de postar a
    MESMA oferta no mesmo canal, cada um com o seu banco de deduplicação;
  - `docker compose` que se recusava a subir enquanto WHATSAPP_GROUP_ID
    estivesse vazio — sendo que o ID só pode ser descoberto com a pilha no
    ar (a Evolution precisa estar de pé para listar os grupos);
  - a porta 8724 publicada apontando para um healthcheck que escuta em
    127.0.0.1 dentro do container, ou seja, uma porta morta;
  - o exemplo `120363XXXXXXXXX@g.us` passando por configuração de verdade e
    fazendo a fila do WhatsApp drenar ofertas para um grupo inexistente;
  - a Evolution (que autentica com a chave que envia mensagem em nome do
    número do Daniel) exposta na internet.

Rodar:
    python tests/test_nuvem_deploy.py
    python -m pytest tests/test_nuvem_deploy.py -v
"""
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(BASE, "deploy")
COMPOSE = os.path.join(DEPLOY, "docker-compose.vps.yml")


def _com_env(**vars_):
    """Contexto simples: aplica variáveis de ambiente e devolve as antigas."""
    antes = {k: os.environ.get(k) for k in vars_}
    for k, v in vars_.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return antes


def _restaurar(antes):
    for k, v in antes.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ── core/papel.py: quem publica e quando ────────────────────────────────────

def test_papel_vazio_e_local():
    """Sem a variável, nada muda: é o PC do Daniel, cujo .env nunca ouviu
    falar de PAPEL. Um módulo novo não pode calar quem já publicava."""
    from core import papel

    antes = _com_env(PAPEL=None)
    try:
        assert papel.papel() == "local"
        pode, _ = papel.pode_publicar()
        assert pode is True, "o PC parou de publicar por causa do papel"
    finally:
        _restaurar(antes)


def test_papel_escrito_errado_cai_no_conservador():
    """Grafia errada só aparece onde alguém TENTOU configurar — ou seja,
    numa instância de nuvem. Cair em `local` liberaria publicação 24h por
    causa de um erro de digitação."""
    from core import papel

    antes = _com_env(PAPEL="nuven")
    try:
        assert papel.papel() == "nuvem"
    finally:
        _restaurar(antes)


def test_nuvem_nao_publica_com_o_pc_ligado():
    """A precondicao "o PC esta publicando" e fixada num repositorio de
    mentira, com um sinal recente. Antes este teste lia o historico REAL do
    checkout — e passou a falhar no dia em que o PC do Daniel ficou 6 dias
    fora do ar, que e justamente quando a nuvem DEVE assumir. Um teste da
    protecao contra duplicata nao pode depender de quem publicou ontem."""
    papel, st = _com_repo(
        [(1, "chore: atualiza site (rastreador-ml) [skip ci]")],
        PAPEL="nuvem", PC_SILENCIO_MAX_H="6",
        HORA_LIGAR="08:30", HORA_DESLIGAR="02:00",
    )
    try:
        meio_dia = datetime(2026, 9, 4, 12, 0)
        pode, motivo = papel.pode_publicar(meio_dia)
        assert pode is False, "a nuvem publicaria junto com o PC — oferta duplicada"
        assert "PC local" in motivo
    finally:
        _solta_repo(papel, st)


def test_nuvem_publica_de_madrugada():
    from core import papel

    antes = _com_env(PAPEL="nuvem", HORA_LIGAR="08:30", HORA_DESLIGAR="02:00")
    try:
        madrugada = datetime(2026, 9, 4, 4, 0)
        pode, _ = papel.pode_publicar(madrugada)
        assert pode is True, "ninguém publicaria de madrugada"
    finally:
        _restaurar(antes)


def test_nuvem_respeita_a_carencia_do_desligamento():
    """Às 02:00 a janela já diz "fora", mas `aguardar_e_desligar.ps1` espera
    até 35 min o bot terminar a rodada — e nesse intervalo o PC continua
    publicando. Este é o caso que a `pc_pode_estar_publicando()` cobre."""
    papel, st = _com_repo(
        [(1, "chore: atualiza site (rastreador-ml) [skip ci]")],
        PAPEL="nuvem", PC_SILENCIO_MAX_H="6",
        HORA_LIGAR="08:30", HORA_DESLIGAR="02:00",
    )
    try:
        dentro_da_carencia = datetime(2026, 9, 4, 2, 20)
        pode, _ = papel.pode_publicar(dentro_da_carencia)
        assert pode is False, "publicou em cima do PC que ainda estava terminando a rodada"

        depois_da_carencia = datetime(2026, 9, 4, 2, 40)
        pode, _ = papel.pode_publicar(depois_da_carencia)
        assert pode is True, "ficou travado depois de a carência passar"
    finally:
        _solta_repo(papel, st)


def test_papeis_extremos():
    from core import papel

    antes = _com_env(PAPEL="desligado")
    try:
        pode, _ = papel.pode_publicar(datetime(2026, 9, 4, 4, 0))
        assert pode is False
    finally:
        _restaurar(antes)

    antes = _com_env(PAPEL="nuvem-exclusiva")
    try:
        pode, _ = papel.pode_publicar(datetime(2026, 9, 4, 12, 0))
        assert pode is True, "o publicador exclusivo ficou travado no meio do dia"
    finally:
        _restaurar(antes)


def test_cli_do_papel_responde_por_codigo_de_saida():
    """É como o workflow do GitHub pergunta — se o exit code parar de
    refletir a decisão, o Actions volta a publicar em cima do PC."""
    env = dict(os.environ, PAPEL="desligado", PYTHONPATH=BASE)
    r = subprocess.run([sys.executable, "-m", "core.papel", "--pode-publicar"],
                       cwd=BASE, env=env, capture_output=True, text=True)
    assert r.returncode == 1, f"papel desligado devia sair 1: {r.stdout} {r.stderr}"

    env["PAPEL"] = "nuvem-exclusiva"
    r = subprocess.run([sys.executable, "-m", "core.papel", "--pode-publicar"],
                       cwd=BASE, env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"papel exclusivo devia sair 0: {r.stdout} {r.stderr}"


def test_os_quatro_processos_consultam_o_papel():
    """A trava só vale se estiver nos 4 lugares que publicam. Um processo
    esquecido publica sozinho e a duplicata volta."""
    for arquivo in ("rastreador.py", "rastreador_amazon.py",
                    "campanha_ferramentas.py", "whatsapp_queue_sender.py"):
        texto = open(os.path.join(BASE, arquivo), encoding="utf-8").read()
        assert "papel.bloqueado()" in texto, f"{arquivo} publica sem consultar o papel"


def test_papel_aparece_no_health():
    """"Por que o servidor está quieto?" precisa ter resposta sem SSH."""
    from core import healthcheck

    estado = healthcheck._status_papel()
    assert "papel" in estado and "pode_publicar" in estado, estado


# ── Prova de vida do PC (o motivo de 04/09/2026) ────────────────────────────

def _repo_falso(commits):
    """Cria um repositorio git de mentira com os commits pedidos.

    `commits` e uma lista de (horas_atras, assunto). Testa o parsing de
    verdade — o `git log` real, com datas reais — em vez de fingir a
    resposta da funcao que se quer testar.
    """
    import subprocess as sp
    import tempfile
    from datetime import timedelta

    pasta = tempfile.mkdtemp(prefix="papel_teste_")

    def git(*args, env_extra=None):
        env = dict(os.environ)
        env.update({
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        })
        env.update(env_extra or {})
        return sp.run(["git", *args], cwd=pasta, capture_output=True,
                      text=True, env=env, check=True)

    git("init", "-q", "-b", "main")
    os.makedirs(os.path.join(pasta, "docs"), exist_ok=True)
    agora = datetime.now()
    for horas, assunto in commits:
        quando = (agora - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%S")
        alvo = os.path.join(pasta, "docs", "offers.json")
        with open(alvo, "a", encoding="utf-8") as f:
            f.write(assunto + "\n")
        git("add", "docs/offers.json")
        git("commit", "-q", "-m", assunto,
            env_extra={"GIT_AUTHOR_DATE": quando, "GIT_COMMITTER_DATE": quando})
    return pasta


def _com_repo(commits, **env):
    """Aponta o core.papel para um repositorio de mentira e limpa o cache."""
    from core import papel

    pasta = _repo_falso(commits)
    antes_base = papel._BASE
    antes_cache = papel._cache_sinal
    papel._BASE = pasta
    papel._cache_sinal = None
    antes_env = _com_env(**env)
    return papel, (antes_base, antes_cache, antes_env)


def _solta_repo(papel, estado):
    antes_base, antes_cache, antes_env = estado
    papel._BASE = antes_base
    papel._cache_sinal = antes_cache
    _restaurar(antes_env)


def test_pc_publicando_ha_pouco_segura_a_nuvem():
    papel, st = _com_repo(
        [(30, "chore: atualiza site (rastreador-ml) [skip ci]"),
         (2, "chore: atualiza site (rastreador-amazon) [skip ci]")],
        PAPEL="nuvem", PC_SILENCIO_MAX_H="6",
        HORA_LIGAR="08:30", HORA_DESLIGAR="02:00",
    )
    try:
        morto, motivo = papel.pc_parece_morto()
        assert morto is False, motivo
        pode, motivo = papel.pode_publicar(datetime(2026, 9, 4, 12, 0))
        assert pode is False, f"publicou junto com o PC vivo: {motivo}"
    finally:
        _solta_repo(papel, st)


def test_pc_calado_ha_dias_libera_a_nuvem():
    """O caso real de 04/09/2026: PC fora do ar ha 6 dias e agendamento da
    nuvem morto ha 5 semanas — os grupos sem oferta nenhuma. Uma trava que so
    olha o relogio manteria a nuvem calada de dia por causa de um PC que nao
    estava publicando."""
    papel, st = _com_repo(
        [(200, "chore: atualiza site (rastreador-ml) [skip ci]"),
         (150, "chore: atualiza ofertas do site [skip ci]")],  # este e do Actions
        PAPEL="nuvem", PC_SILENCIO_MAX_H="6",
        HORA_LIGAR="08:30", HORA_DESLIGAR="02:00",
    )
    try:
        morto, motivo = papel.pc_parece_morto()
        assert morto is True, motivo
        pode, motivo = papel.pode_publicar(datetime(2026, 9, 4, 12, 0))
        assert pode is True, f"a nuvem ficou calada com o PC morto: {motivo}"
        assert "pelo menos" in motivo or "nao publica" in motivo
    finally:
        _solta_repo(papel, st)


def test_commit_do_actions_nao_conta_como_sinal_do_PC():
    """Se a marca do Actions valesse como prova de vida do PC, a nuvem se
    calaria por causa da propria publicacao anterior — e nunca mais voltaria."""
    papel, st = _com_repo(
        [(1, "chore: atualiza ofertas do site [skip ci]"),
         (2, "chore: atualiza ofertas do site (servidor) [skip ci]")],
        PAPEL="nuvem", PC_SILENCIO_MAX_H="6",
    )
    try:
        # Historico curto de mais para concluir silencio -> "nao da para saber"
        assert papel.horas_desde_sinal_do_pc() is None
    finally:
        _solta_repo(papel, st)


def test_sem_historico_suficiente_nao_age_no_escuro():
    """Checkout raso do Actions: `git log` devolve quase nada. "Nao consegui
    olhar" nunca pode virar "o PC esta morto" — mesmo cuidado que o
    supervisor tomou com o psutil ausente."""
    papel, st = _com_repo(
        [(1, "chore: atualiza ofertas do site [skip ci]")],
        PAPEL="nuvem", PC_SILENCIO_MAX_H="6",
        HORA_LIGAR="08:30", HORA_DESLIGAR="02:00",
    )
    try:
        assert papel.horas_desde_sinal_do_pc() is None
        morto, _ = papel.pc_parece_morto()
        assert morto is False
        pode, _ = papel.pode_publicar(datetime(2026, 9, 4, 12, 0))
        assert pode is False, "publicou sem conseguir confirmar que o PC estava fora"
    finally:
        _solta_repo(papel, st)


def test_checagem_de_silencio_pode_ser_desligada():
    papel, st = _com_repo(
        [(500, "chore: atualiza site (rastreador-ml) [skip ci]")],
        PAPEL="nuvem", PC_SILENCIO_MAX_H="0",
        HORA_LIGAR="08:30", HORA_DESLIGAR="02:00",
    )
    try:
        morto, _ = papel.pc_parece_morto()
        assert morto is False
        pode, _ = papel.pode_publicar(datetime(2026, 9, 4, 12, 0))
        assert pode is False, "PC_SILENCIO_MAX_H=0 deveria manter o comportamento antigo"
    finally:
        _solta_repo(papel, st)


def test_workflow_busca_historico_suficiente():
    """Sem `fetch-depth`, o checkout do Actions traz 1 commit e a prova de
    vida do PC responde sempre "nao sei" — a correcao acima viraria no-op."""
    texto = open(os.path.join(BASE, ".github", "workflows", "bot.yml"),
                 encoding="utf-8").read()
    achado = re.search(r"fetch-depth:\s*(\d+)", texto)
    assert achado, "bot.yml voltou ao checkout raso"
    assert int(achado.group(1)) >= 50, f"fetch-depth curto demais: {achado.group(1)}"


def test_um_gravador_so_para_o_banco_de_deduplicacao():
    """`actions/cache@v4` salva sozinho no fim do job, com a MESMA chave do
    passo explicito de salvar — dois gravadores na mesma chave. Medido na
    execucao 256: "Unable to reserve cache with key sqlite-db-v2-256". O dado
    em disputa e a deduplicacao, cuja perda REPUBLICA as ofertas no canal."""
    texto = open(os.path.join(BASE, ".github", "workflows", "bot.yml"),
                 encoding="utf-8").read()
    usos = re.findall(r"^\s*uses:\s*(actions/cache\S*)", texto, re.M)
    gravadores = [u for u in usos if u.endswith("/save@v4") or re.match(r"^actions/cache@", u)]
    assert len(gravadores) == 1, f"mais de um gravador de cache: {usos}"
    assert gravadores[0].endswith("/save@v4"), gravadores


def test_timer_do_servidor_nao_mascara_falha():
    """`SuccessExitStatus=0 1` fazia `systemctl status` dizer "success" com a
    atualizacao falhando. Uma unidade oneshot que falha nao impede o proximo
    disparo do timer — mascarar so escondia o problema."""
    texto = open(os.path.join(DEPLOY, "instalar_timers.sh"), encoding="utf-8").read()
    assert "SuccessExitStatu" + "s=" not in texto, (
        "o mascaramento de saida de erro voltou para as unidades systemd"
    )


# ── Placeholder do grupo do WhatsApp ────────────────────────────────────────

def test_jid_de_exemplo_nao_liga_o_whatsapp():
    """`120363XXXXXXXXX@g.us` não começa com nenhum prefixo de placeholder e
    passava por configuração real: a fila drenava uma oferta a cada 30-45
    min para um grupo que não existe, marcando cada uma como enviada."""
    from integrations.whatsapp_sender import _placeholder

    assert _placeholder("120363XXXXXXXXX@g.us") is True
    assert _placeholder("cole_aqui_o_id_do_grupo") is True
    # E um JID de verdade continua valendo.
    assert _placeholder("120363028123456789@g.us") is False


# ── docker-compose do servidor ──────────────────────────────────────────────

def _compose_texto():
    return open(COMPOSE, encoding="utf-8").read()


def test_compose_nao_exige_o_id_do_grupo_para_subir():
    """Impasse real da versão anterior: o compose recusava subir sem
    WHATSAPP_GROUP_ID, e o ID só se descobre com a Evolution no ar. Sem
    grupo o WhatsApp fica desligado e o Telegram publica (Regra 6)."""
    texto = _compose_texto()
    assert "WHATSAPP_GROUP_ID:" not in texto or "${WHATSAPP_GROUP_ID:?" not in texto, (
        "o compose voltou a travar a subida por causa do ID do grupo"
    )


def test_compose_publica_portas_so_no_loopback():
    """A 8080 serve o /manager da Evolution, que autentica com a MESMA chave
    que envia mensagem pelo número do Daniel. Aberta na internet, é um painel
    de controle do WhatsApp dele exposto."""
    texto = _compose_texto()
    portas = re.findall(r'^\s*-\s*"([^"]+)"\s*$', texto, re.M)
    publicadas = [p for p in portas if re.match(r"^[\d.:]+$", p)]
    assert publicadas, "nenhuma porta encontrada — o teste perdeu o alvo"
    for p in publicadas:
        assert p.startswith("127.0.0.1:"), f"porta exposta para fora: {p}"


def test_compose_liga_o_healthcheck_na_interface_certa():
    """Publicar 8724 sem trocar o bind é publicar uma porta morta: dentro do
    container o healthcheck escuta em 127.0.0.1 por padrão."""
    texto = _compose_texto()
    assert "HEALTHCHECK_BIND: 0.0.0.0" in texto
    assert "127.0.0.1:8724:8724" in texto


def test_container_consegue_ler_o_historico_do_repositorio():
    """A prova de vida do PC le o historico com `git log`. Dentro do
    container faltavam as DUAS pontas: o binario `git` (nao estava na imagem)
    e o proprio `.git` (o .dockerignore o exclui de proposito). Sem elas a
    checagem responde "nao sei" para sempre e o servidor espera calado por um
    PC que pode estar fora do ar ha semanas."""
    dockerfile = open(os.path.join(DEPLOY, "Dockerfile"), encoding="utf-8").read()
    assert re.search(r"apt-get install[^\n]*(\n[^\n]*)*?\bgit\b", dockerfile), (
        "o `git` sumiu da imagem"
    )
    compose = _compose_texto()
    montagens = re.findall(r"\.\./\.git:/app/\.git:ro", compose)
    assert montagens, "o compose parou de montar o .git no container"


def test_sem_git_o_papel_avisa_em_vez_de_calar():
    """Degradar para "espero dentro da janela" e seguro; degradar em silencio
    nao e. Este projeto ja perdeu semanas para falhas que nao apareciam."""
    import logging as _logging
    import tempfile

    from core import papel

    registros = []

    class _Coletor(_logging.Handler):
        def emit(self, r):
            registros.append(r)

    log = _logging.getLogger("papel")
    h = _Coletor()
    log.addHandler(h)
    antes_base, antes_cache = papel._BASE, papel._cache_sinal
    try:
        papel._BASE = tempfile.mkdtemp()   # pasta sem .git, como /app sem a montagem
        papel._cache_sinal = None
        assert papel.horas_desde_sinal_do_pc() is None
        avisos = [r for r in registros if r.levelno >= _logging.WARNING]
        assert avisos, "a checagem virou no-op sem uma linha de aviso"
    finally:
        log.removeHandler(h)
        papel._BASE, papel._cache_sinal = antes_base, antes_cache


def test_compose_define_o_fuso():
    """Droplet novo nasce em UTC: sem TZ a janela 08:30-02:00 escorrega 3h e
    o papel `nuvem` publica por cima do PC ligado, sem erro no log."""
    texto = _compose_texto()
    assert "TZ:" in texto and "America/Sao_Paulo" in texto


def test_compose_repassa_o_env_inteiro():
    """Listar variável por variável fazia qualquer chave nova do .env
    (N8N_TOKEN, ADMIN_CHAT_ID, PUBLICAR_SEM_FOTO...) não existir dentro do
    container — o operador editava o .env e nada mudava, sem erro nenhum."""
    texto = _compose_texto()
    assert "env_file:" in texto and "../.env" in texto


# ── Scripts do servidor ─────────────────────────────────────────────────────

def test_scripts_do_deploy_tem_sintaxe_valida():
    scripts = [f for f in os.listdir(DEPLOY) if f.endswith(".sh")]
    assert scripts, "nenhum script encontrado em deploy/"
    for nome in scripts:
        r = subprocess.run(["bash", "-n", os.path.join(DEPLOY, nome)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"{nome} não passa em `bash -n`: {r.stderr}"


def test_scripts_nao_leem_env_com_cut():
    """`cut -d= -f2` trunca no primeiro `=`, e uma chave gerada por
    `secrets.token_urlsafe` pode conter um. O sintoma era a Evolution
    respondendo 401 sem nada no output explicando por quê."""
    for nome in os.listdir(DEPLOY):
        if not nome.endswith(".sh"):
            continue
        texto = open(os.path.join(DEPLOY, nome), encoding="utf-8").read()
        for linha in texto.splitlines():
            if linha.lstrip().startswith("#"):
                continue
            assert not re.search(r"\.env.*cut -d=", linha), f"{nome}: {linha.strip()}"


def test_ler_env_devolve_o_valor_inteiro():
    """Prova funcional do substituto do `cut -d= -f2`: a chave da Evolution
    e gerada por `secrets.token_urlsafe` e pode conter `=`; truncada, a API
    responde 401 sem nada no output explicando por que."""
    import subprocess as sp
    import tempfile

    casos = {
        "EVOLUTION_API_KEY": "abc=def==ghi/jk+lm",
        "WHATSAPP_INSTANCE": "botofertas",
        "COM_ASPAS": '"valor entre aspas"',
        "REPETIDA": "primeiro",
    }
    with tempfile.TemporaryDirectory() as tmp:
        env_path = os.path.join(tmp, ".env")
        with open(env_path, "w", encoding="utf-8") as f:
            for k, v in casos.items():
                f.write(f"{k}={v}\n")
            f.write("REPETIDA=ultimo\n")   # a ultima ocorrencia e a que vale

        script = (
            f'ENV_FILE="{env_path}"\n'
            "ler_env() {\n"
            '  local chave="$1"\n'
            '  sed -n "s/^[[:space:]]*${chave}[[:space:]]*=//p" "$ENV_FILE" \\\n'
            "    | tail -n 1 \\\n"
            "    | sed -e 's/^\"\\(.*\\)\"$/\\1/' -e \"s/^'\\(.*\\)'$/\\1/\"\n"
            "}\n"
            'for k in EVOLUTION_API_KEY WHATSAPP_INSTANCE COM_ASPAS REPETIDA; do\n'
            '  echo "$k=$(ler_env "$k")"\n'
            "done\n"
        )
        r = sp.run(["bash", "-c", script], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        saida = dict(l.split("=", 1) for l in r.stdout.strip().splitlines())

    assert saida["EVOLUTION_API_KEY"] == "abc=def==ghi/jk+lm", saida
    assert saida["WHATSAPP_INSTANCE"] == "botofertas", saida
    assert saida["COM_ASPAS"] == "valor entre aspas", saida
    assert saida["REPETIDA"] == "ultimo", saida

    # E a implementacao testada acima e mesmo a que esta no repositorio.
    comum = open(os.path.join(DEPLOY, "_comum.sh"), encoding="utf-8").read()
    assert 'sed -n "s/^[[:space:]]*${chave}[[:space:]]*=//p"' in comum, (
        "deploy/_comum.sh mudou a leitura do .env — atualize este teste junto"
    )


def test_nenhum_segredo_versionado_no_deploy():
    """Mesma trava que já existe para os workflows do n8n (Regra 13)."""
    suspeitos = re.compile(
        r"(apikey|api_key|token|secret|senha|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}",
        re.I,
    )
    for raiz, _, arquivos in os.walk(DEPLOY):
        for nome in arquivos:
            caminho = os.path.join(raiz, nome)
            texto = open(caminho, encoding="utf-8", errors="replace").read()
            achado = suspeitos.search(texto)
            assert not achado, f"possível segredo em {nome}: {achado.group(0)[:40]}"


def test_env_example_do_deploy_nao_repete_a_raiz():
    """Duplicar o .env.example da raiz criaria duas listas para envelhecer
    separadamente. Aqui só entra o que é específico do servidor."""
    texto = open(os.path.join(DEPLOY, ".env.example"), encoding="utf-8").read()
    for chave in ("EVOLUTION_API_KEY", "PAPEL", "TZ"):
        assert chave in texto, f"{chave} sumiu do exemplo do servidor"
    for chave in ("TOKEN_TELEGRAM", "ML_APP_SECRET", "ANTHROPIC_API_KEY"):
        assert f"\n{chave}=" not in texto, (
            f"{chave} foi duplicada do .env.example da raiz"
        )


def test_dockerfile_fixa_a_distro():
    """`python:3.13-slim` sozinho segue o Debian estável do dia; quando ele
    virou trixie, os pacotes do Chromium mudaram de nome (libasound2 ->
    libasound2t64) e um build que funcionava ontem passa a falhar."""
    texto = open(os.path.join(DEPLOY, "Dockerfile"), encoding="utf-8").read()
    assert re.search(r"^FROM python:\S+-(bookworm|bullseye|trixie)", texto, re.M), (
        "a imagem base voltou a seguir a distro móvel"
    )
    assert "tzdata" in texto, "sem tzdata o TZ do compose é ignorado"


def test_workflow_do_actions_pergunta_ao_papel():
    """A regra "só publica com o PC dormindo" morava escrita em shell dentro
    do workflow. Com três publicadores, ela tem que sair do mesmo módulo."""
    texto = open(os.path.join(BASE, ".github", "workflows", "bot.yml"),
                 encoding="utf-8").read()
    assert "core.papel --pode-publicar" in texto
    assert "PAPEL:" in texto


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
