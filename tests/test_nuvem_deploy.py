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
    from core import papel

    antes = _com_env(PAPEL="nuvem", HORA_LIGAR="08:30", HORA_DESLIGAR="02:00")
    try:
        meio_dia = datetime(2026, 9, 4, 12, 0)
        pode, motivo = papel.pode_publicar(meio_dia)
        assert pode is False, "a nuvem publicaria junto com o PC — oferta duplicada"
        assert "PC local" in motivo
    finally:
        _restaurar(antes)


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
    from core import papel

    antes = _com_env(PAPEL="nuvem", HORA_LIGAR="08:30", HORA_DESLIGAR="02:00")
    try:
        dentro_da_carencia = datetime(2026, 9, 4, 2, 20)
        pode, _ = papel.pode_publicar(dentro_da_carencia)
        assert pode is False, "publicou em cima do PC que ainda estava terminando a rodada"

        depois_da_carencia = datetime(2026, 9, 4, 2, 40)
        pode, _ = papel.pode_publicar(depois_da_carencia)
        assert pode is True, "ficou travado depois de a carência passar"
    finally:
        _restaurar(antes)


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
