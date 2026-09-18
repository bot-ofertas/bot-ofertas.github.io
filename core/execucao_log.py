# -*- coding: utf-8 -*-
"""
LOG DE EXECUÇÃO — um bloco de texto por execução, na Área de Trabalho.

Pedido do Daniel: toda execução do bot tem que deixar registrado, em lugar
que ele abra com dois cliques, **se deu erro e em que ponto** — sem abrir
terminal, sem ler JSON, sem saber onde fica a pasta do projeto.

Este módulo é o lado "uma linha por execução" do registro. O lado "um bloco
por erro, com traceback" continua em `core/error_logger.py`, e os dois
escrevem na MESMA pasta da Área de Trabalho:

    <Área de Trabalho>/problemas de execução/
        log de execução.txt        ← este módulo: uma execução por bloco
        erros detalhados.txt       ← core/error_logger.py: erro + traceback
        relatório de problemas.txt ← gerar_relatorio_problemas.py (24h)
        em andamento/              ← execução ainda rodando (ver abaixo)

Uso típico (rastreador.py, campanha, fila do WhatsApp):

    from core import execucao_log

    with execucao_log.abrir_execucao("rastreador ML (uma rodada)"):
        execucao_log.etapa("banco de dados inicializado")
        ...
        execucao_log.erro("envio para o WhatsApp", exc=e)

Ao sair do `with`, o bloco inteiro é anexado ao log com o veredito
("SEM ERROS" ou "ERRO — falhou em ..."). Uma exceção que escape do `with`
é registrada como ponto de falha e continua subindo.

Por que o bloco só é anexado no fim: `startup.py` sobe 4 processos que
publicam ao mesmo tempo (ML, Amazon, ferramentas, fila do WhatsApp). Se cada
um escrevesse linha a linha no arquivo único, o log viraria quatro execuções
picadas e intercaladas — ilegível justamente para quem ele existe. Enquanto
roda, cada processo mantém seu bloco em `em andamento/<script>-<pid>.txt`;
quem for morto no meio (desligamento da Regra 15, queda de energia, kill)
deixa o arquivo lá, e a próxima execução o recolhe para o log marcado como
INTERROMPIDA. Execução que morre não pode parecer execução que deu certo.
"""
from __future__ import annotations

import atexit
import os
import platform
import sys
import threading
import time
import traceback
from datetime import datetime

from core.segredos import redigir

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NOME_PASTA = "problemas de execução"
NOME_LOG = "log de execução.txt"
NOME_LOG_ANTERIOR = "log de execução (anterior).txt"
NOME_ANDAMENTO = "em andamento"
NOME_LEIA_ME = "LEIA-ME.txt"

# Ponteiro lido pelo start.ps1 e pelo status.ps1 para mostrar o caminho certo
# sem precisar repetir em PowerShell a lógica de achar a Área de Trabalho
# (que muda com OneDrive e com o nome localizado da pasta).
PONTEIRO = os.path.join(BASE, "data", "caminho_log_execucao.txt")

LIMITE_BYTES = 2_000_000          # acima disso, gira para "(anterior)"
_IDADE_ORFAO_S = 6 * 3600         # ver _recuperar_interrompidas()

_LARGURA = 74
# Recuo das linhas de detalhe: alinha com o texto da etapa
# ("  HH:MM:SS  [marca] texto").
_INDENTE = " " * 20
_lock = threading.Lock()
_pasta: str | None = None
_pilha: list["Execucao"] = []
_ganchos_instalados = False


# ── Onde gravar ──────────────────────────────────────────────────────────────

def _area_de_trabalho_windows() -> str:
    """Área de Trabalho pela API do Windows.

    `%USERPROFILE%\\Desktop` erra nos dois casos que mais aparecem nesta
    máquina: quando o OneDrive assume a pasta (vira
    `%USERPROFILE%\\OneDrive\\Desktop`) e quando o Windows em português
    mostra "Área de Trabalho". A API devolve o caminho real nos dois.
    """
    if sys.platform != "win32":
        return ""
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
        guid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                     (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                          0x9A, 0x87, 0xC6, 0x41))
        ponteiro = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(ponteiro)) != 0:
            return ""
        caminho = ponteiro.value or ""
        ctypes.windll.ole32.CoTaskMemFree(ponteiro)
        return caminho
    except Exception:
        return ""


def _area_de_trabalho() -> str:
    api = _area_de_trabalho_windows()
    if api and os.path.isdir(api):
        return api
    lar = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    for raiz in (lar, os.path.join(lar, "OneDrive")):
        for nome in ("Desktop", "Área de Trabalho", "Area de Trabalho"):
            candidato = os.path.join(raiz, nome)
            if os.path.isdir(candidato):
                return candidato
    return ""


def pasta_problemas(recarregar: bool = False) -> str:
    """Pasta "problemas de execução" na Área de Trabalho, já criada.

    `BOT_PASTA_PROBLEMAS` no .env manda em tudo — é como o container do
    servidor (deploy/, Linux sem Área de Trabalho) aponta o log para um
    volume montado em vez de cair no fallback dentro do projeto.
    """
    global _pasta
    if _pasta and not recarregar:
        return _pasta

    tentativas: list[str] = []
    forcada = (os.environ.get("BOT_PASTA_PROBLEMAS") or "").strip()
    if forcada:
        tentativas.append(forcada)
    else:
        mesa = _area_de_trabalho()
        if mesa:
            tentativas.append(os.path.join(mesa, NOME_PASTA))
    # Último recurso: dentro do projeto. Sem Área de Trabalho (servidor
    # Linux, serviço do Windows sem perfil carregado) o registro não pode
    # simplesmente deixar de existir.
    tentativas.append(os.path.join(BASE, "data", NOME_PASTA))

    for caminho in tentativas:
        try:
            os.makedirs(os.path.join(caminho, NOME_ANDAMENTO), exist_ok=True)
        except Exception:
            continue
        _pasta = caminho
        _escrever_leia_me(caminho)
        _escrever_ponteiro(caminho)
        _migrar_arquivos_antigos(caminho)
        return caminho

    _pasta = os.path.join(BASE, "data")
    return _pasta


def caminho_log() -> str:
    return os.path.join(pasta_problemas(), NOME_LOG)


def caminho_erros_detalhados() -> str:
    """Arquivo do `core/error_logger.py` — mesma pasta, um bloco por erro."""
    return os.path.join(pasta_problemas(), "erros detalhados.txt")


def caminho_relatorio() -> str:
    """Arquivo do `gerar_relatorio_problemas.py` — resumo das últimas 24h."""
    return os.path.join(pasta_problemas(), "relatório de problemas.txt")


def _escrever_ponteiro(pasta: str) -> None:
    try:
        os.makedirs(os.path.dirname(PONTEIRO), exist_ok=True)
        with open(PONTEIRO, "w", encoding="utf-8") as f:
            f.write(pasta + "\n")
            f.write(os.path.join(pasta, NOME_LOG) + "\n")
    except Exception:
        pass


def _escrever_leia_me(pasta: str) -> None:
    arquivo = os.path.join(pasta, NOME_LEIA_ME)
    if os.path.exists(arquivo):
        return
    try:
        with open(arquivo, "w", encoding="utf-8") as f:
            f.write(
                "PROBLEMAS DE EXECUÇÃO — Bot Ofertas\n"
                "===================================\n\n"
                "Esta pasta é escrita pelo próprio bot. Nada aqui precisa ser\n"
                "editado ou apagado à mão.\n\n"
                f"{NOME_LOG}\n"
                "    Uma execução por bloco, em ordem (a mais recente fica no\n"
                "    fim do arquivo). Cada bloco termina em RESULTADO, que diz\n"
                "    SEM ERROS, ERRO (com o ponto onde falhou) ou INTERROMPIDA\n"
                "    (o processo morreu antes de terminar — PC desligado, queda\n"
                "    de energia, encerramento forçado).\n\n"
                "erros detalhados.txt\n"
                "    Um bloco por erro, com o traceback completo. É o arquivo\n"
                "    para mandar para quem vai corrigir.\n\n"
                "relatório de problemas.txt\n"
                "    Resumo das últimas 24h (erros por tipo, quarentena e o\n"
                "    estado geral do sistema). Atualizado toda madrugada.\n\n"
                f"{NOME_ANDAMENTO}/\n"
                "    Execução que está rodando agora. O arquivo desaparece\n"
                "    quando ela termina; se sobrar algum, a execução seguinte o\n"
                "    recolhe para o log como INTERROMPIDA.\n"
            )
    except Exception:
        pass


def _migrar_arquivos_antigos(pasta: str) -> None:
    """Recolhe para a pasta os arquivos soltos que ficavam na Área de Trabalho.

    Antes desta pasta existir, o bot escrevia dois .txt direto na mesa:
    "Problemas de execução para corrigir.txt" (error_logger) e
    "problemas de execucao.txt" (relatório). Deixá-los lá seria a mesma
    informação em três lugares, com o nome do arquivo antigo colidindo com o
    nome da pasta nova. Best-effort: se não der para mover, fica como está.
    """
    mesa = os.path.dirname(pasta)
    if not mesa or not os.path.isdir(mesa):
        return
    for antigo, novo in (
        ("Problemas de execução para corrigir.txt", os.path.basename(caminho_erros_detalhados())),
        ("problemas de execucao.txt", os.path.basename(caminho_relatorio())),
    ):
        origem = os.path.join(mesa, antigo)
        destino = os.path.join(pasta, novo)
        if not os.path.isfile(origem) or os.path.exists(destino):
            continue
        try:
            os.replace(origem, destino)
        except Exception:
            pass


# ── Formatação ───────────────────────────────────────────────────────────────

def _duracao(segundos: float) -> str:
    segundos = int(segundos)
    if segundos < 60:
        return f"{segundos}s"
    if segundos < 3600:
        return f"{segundos // 60}min {segundos % 60}s"
    return f"{segundos // 3600}h {(segundos % 3600) // 60}min"


def _onde(exc: BaseException | None) -> str:
    """Arquivo, função e linha de ONDE a exceção estourou — não de onde ela
    foi capturada. É a pergunta que o Daniel faz ("em que ponto travou?"),
    e o último quadro do traceback é a única resposta honesta: o
    `except` costuma estar num arquivo que não tem nada a ver com a falha.
    """
    if exc is None:
        return ""
    quadros = traceback.extract_tb(exc.__traceback__)
    if not quadros:
        return ""
    q = quadros[-1]
    return f"{os.path.basename(q.filename)} → {q.name}(), linha {q.lineno}"


class Execucao:
    """Uma execução em andamento. Criada por `abrir_execucao()`."""

    def __init__(self, titulo: str, comando: str = "", contexto: dict | None = None):
        self.titulo = titulo
        self.comando = comando or " ".join(
            [os.path.basename(sys.argv[0] or "python")] + sys.argv[1:])
        self.contexto = contexto or {}
        self.inicio = datetime.now()
        self._t0 = time.time()
        self.linhas: list[str] = []
        self.erros: list[str] = []
        self.fechada = False
        self._resumo = ""
        self._ultimo_erro: tuple[str, str, float] = ("", "", 0.0)
        base = os.path.basename(sys.argv[0] or "python").replace(" ", "_") or "python"
        self._andamento = os.path.join(
            pasta_problemas(), NOME_ANDAMENTO, f"{base}-{os.getpid()}.txt")

    # -- cabeçalho/corpo ----------------------------------------------------

    def _cabecalho(self) -> list[str]:
        maquina = platform.node() or "?"
        py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        linhas = [
            "=" * _LARGURA,
            f"EXECUÇÃO   : {self.titulo}",
            f"Início     : {self.inicio.strftime('%d/%m/%Y %H:%M:%S')}",
            f"Onde       : {maquina} · PID {os.getpid()} · Python {py}",
            f"Comando    : {redigir(self.comando)}",
        ]
        for chave, valor in self.contexto.items():
            linhas.append(f"{str(chave)[:10].ljust(10)} : {redigir(str(valor))[:200]}")
        linhas.append("-" * _LARGURA)
        return linhas

    def _texto_parcial(self) -> str:
        corpo = self.linhas or ["  (nenhuma etapa registrada ainda)"]
        return "\n".join(self._cabecalho() + corpo) + "\n"

    def _salvar_andamento(self) -> None:
        try:
            with open(self._andamento, "w", encoding="utf-8") as f:
                f.write(self._texto_parcial())
        except Exception:
            pass  # o registro é best-effort: nunca derruba quem está publicando

    # -- API ----------------------------------------------------------------

    def etapa(self, descricao: str, ok: bool = True, detalhe: str = "") -> None:
        """Registra um ponto alcançado da execução."""
        marca = ("[ok]" if ok else "[aviso]").ljust(7)
        self.linhas.append(
            f"  {datetime.now().strftime('%H:%M:%S')}  {marca} {redigir(str(descricao))}")
        if detalhe:
            self.linhas.append(f"{_INDENTE}{redigir(str(detalhe))[:300]}")
        self._salvar_andamento()

    def erro(self, descricao: str, exc: BaseException | None = None,
             mensagem: str = "", onde: str = "") -> None:
        """Registra o PONTO DE FALHA da execução.

        A mesma falha chega aqui por dois caminhos de propósito: o chamador
        registra explicitamente (garantia de que o bloco nunca diga "SEM
        ERROS" numa rodada que quebrou, mesmo se o banco estiver travado) e
        `db.registrar_erro()` -> `log_erro()` também registra. O de-dup de
        5s existe para isso não virar duas linhas iguais coladas no bloco;
        a mesma falha repetida minutos depois continua aparecendo, porque
        duas tentativas falhas são duas informações.
        """
        texto = redigir(str(mensagem or (exc and str(exc)) or "sem detalhe"))[:300]
        agora = time.time()
        anterior = self._ultimo_erro
        if (anterior[0], anterior[1]) == (str(descricao), texto) and agora - anterior[2] < 5:
            return
        self._ultimo_erro = (str(descricao), texto, agora)
        tipo = type(exc).__name__ if exc is not None else ""
        self.linhas.append(
            f"  {datetime.now().strftime('%H:%M:%S')}  {'[ERRO]'.ljust(7)} "
            f"{redigir(str(descricao))}")
        self.linhas.append(f"{_INDENTE}{tipo + ': ' if tipo else ''}{texto}")
        local = onde or _onde(exc)
        if local:
            self.linhas.append(f"{_INDENTE}onde: {local}")
        self.erros.append(str(descricao))
        self._salvar_andamento()

    def definir_resumo(self, texto: str) -> None:
        """Linha de resumo do bloco (contadores da rodada, por exemplo)."""
        self._resumo = str(texto)

    def fechar(self, resumo: str = "", interrompida: bool = False) -> None:
        if self.fechada:
            return
        self.fechada = True
        resumo = resumo or self._resumo
        linhas = self._cabecalho() + (self.linhas or ["  (nenhuma etapa registrada)"])
        linhas.append("-" * _LARGURA)
        if interrompida:
            linhas.append(
                'RESULTADO  : INTERROMPIDA — o processo terminou sem registrar o fim')
            linhas.append(
                "             (PC desligado, queda de energia ou encerramento forçado).")
            linhas.append(
                "             A última linha acima é o ponto em que parou.")
        elif self.erros:
            linhas.append(f'RESULTADO  : ERRO — falhou em "{self.erros[0]}"')
            if len(self.erros) > 1:
                linhas.append(f"             {len(self.erros)} erros nesta execução: "
                              + ", ".join(self.erros[:5]))
            linhas.append('             Traceback completo em "erros detalhados.txt".')
        else:
            linhas.append("RESULTADO  : SEM ERROS — a execução terminou normalmente.")
        if resumo:
            linhas.append(f"Resumo     : {redigir(str(resumo))[:300]}")
        linhas.append(f"Fim        : {datetime.now().strftime('%H:%M:%S')} "
                      f"(duração {_duracao(time.time() - self._t0)})")
        linhas.append("")

        _anexar("\n".join(linhas) + "\n")
        try:
            os.remove(self._andamento)
        except Exception:
            pass
        if _pilha and _pilha[-1] is self:
            _pilha.pop()
        elif self in _pilha:
            _pilha.remove(self)

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> "Execucao":
        return self

    def __exit__(self, tipo, valor, tb) -> bool:
        if valor is not None:
            # O nome do tipo entra no rótulo porque ele é o que vai para a
            # linha RESULTADO — "falhou em erro não tratado" não diria nada.
            self.erro(f"erro não tratado ({type(valor).__name__})", exc=valor)
        self.fechar()
        return False  # a exceção continua subindo


# ── Funções de módulo ────────────────────────────────────────────────────────

def _anexar(bloco: str) -> None:
    """Anexa um bloco pronto ao log principal, numa única escrita.

    Uma escrita por bloco (e não por linha) é o que mantém legível o log de
    quatro processos publicando ao mesmo tempo — ver a nota do topo.
    """
    try:
        _rotacionar()
        with _lock:
            with open(caminho_log(), "a", encoding="utf-8") as f:
                f.write(bloco)
    except Exception:
        pass


def _rotacionar() -> None:
    caminho = caminho_log()
    try:
        if os.path.getsize(caminho) < LIMITE_BYTES:
            return
        os.replace(caminho, os.path.join(pasta_problemas(), NOME_LOG_ANTERIOR))
    except OSError:
        pass


def _processo_vivo(pid: int) -> bool | None:
    """True/False, ou None quando não há como saber.

    No Windows NÃO dá para usar `os.kill(pid, 0)`: lá o sinal 0 cai em
    `TerminateProcess(handle, 0)` — a checagem MATARIA o processo, que
    neste projeto é justamente um rastreador publicando. Daí o caminho por
    psutil e, sem ele, por OpenProcess/GetExitCodeProcess.
    """
    try:
        import psutil  # noqa: PLC0415
        return psutil.pid_exists(pid)
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes  # noqa: PLC0415
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            codigo = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(codigo))
            ctypes.windll.kernel32.CloseHandle(handle)
            return bool(codigo.value == STILL_ACTIVE) if ok else None
        except Exception:
            return None
    try:
        os.kill(pid, 0)  # POSIX: sinal 0 só consulta, não mata
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return None


def _pid_do_arquivo(nome: str) -> int:
    try:
        return int(os.path.splitext(nome)[0].rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return -1


def _recuperar_interrompidas() -> None:
    """Recolhe para o log as execuções que morreram sem fechar o bloco."""
    pasta = os.path.join(pasta_problemas(), NOME_ANDAMENTO)
    try:
        nomes = sorted(os.listdir(pasta))
    except OSError:
        return
    for nome in nomes:
        if not nome.endswith(".txt"):
            continue
        caminho = os.path.join(pasta, nome)
        pid = _pid_do_arquivo(nome)
        if pid == os.getpid():
            continue
        vivo = _processo_vivo(pid) if pid > 0 else False
        if vivo is True:
            continue  # outro rastreador está rodando agora — não é órfão
        try:
            idade = time.time() - os.path.getmtime(caminho)
            if vivo is None and idade < _IDADE_ORFAO_S:
                continue  # sem como checar o PID: só desiste depois de horas
            with open(caminho, encoding="utf-8") as f:
                texto = f.read().rstrip("\n")
        except OSError:
            continue
        _anexar("\n".join([
            texto,
            "-" * _LARGURA,
            "RESULTADO  : INTERROMPIDA — o processo terminou sem registrar o fim",
            "             (PC desligado, queda de energia ou encerramento forçado).",
            "             A última linha acima é o ponto em que parou.",
            "",
        ]) + "\n")
        try:
            os.remove(caminho)
        except OSError:
            pass


def _instalar_ganchos() -> None:
    """Fecha o bloco mesmo quando ninguém chama `fechar()`.

    Cobre os dois jeitos de o processo acabar sem passar pelo `with`: exceção
    que sobe até o topo (excepthook) e `sys.exit`/fim do script (atexit).
    """
    global _ganchos_instalados
    if _ganchos_instalados:
        return
    _ganchos_instalados = True

    anterior = sys.excepthook

    def _excepthook(tipo, valor, tb):
        for ex in list(reversed(_pilha)):
            if not ex.fechada:
                ex.erro("processo encerrado por erro não tratado", exc=valor)
                ex.fechar()
        anterior(tipo, valor, tb)

    sys.excepthook = _excepthook
    atexit.register(_fechar_pendentes)


def _fechar_pendentes() -> None:
    for ex in list(reversed(_pilha)):
        if not ex.fechada:
            ex.fechar(interrompida=True)


def abrir_execucao(titulo: str, comando: str = "",
                   contexto: dict | None = None) -> Execucao:
    """Abre um bloco de execução. Usar como context manager."""
    _instalar_ganchos()
    _recuperar_interrompidas()
    ex = Execucao(titulo, comando, contexto)
    _pilha.append(ex)
    ex._salvar_andamento()
    return ex


def execucao_atual() -> Execucao | None:
    return _pilha[-1] if _pilha else None


def etapa(descricao: str, ok: bool = True, detalhe: str = "") -> None:
    """Registra uma etapa na execução aberta (no-op se não houver nenhuma).

    É o que permite a `integrations/whatsapp_sender.py` e ao
    `core/error_logger.py` alimentarem o bloco sem receber o objeto da
    execução por parâmetro em toda a cadeia de chamadas.
    """
    ex = execucao_atual()
    if ex is not None:
        ex.etapa(descricao, ok=ok, detalhe=detalhe)


def erro(descricao: str, exc: BaseException | None = None,
         mensagem: str = "", onde: str = "") -> None:
    """Registra o ponto de falha na execução aberta (no-op sem execução)."""
    ex = execucao_atual()
    if ex is not None:
        ex.erro(descricao, exc=exc, mensagem=mensagem, onde=onde)


def resumo(texto: str) -> None:
    """Define a linha "Resumo" do bloco aberto (no-op sem execução)."""
    ex = execucao_atual()
    if ex is not None:
        ex.definir_resumo(texto)


def ultimo_bloco() -> str:
    """Último bloco do log, como texto. Usado pelo `status.ps1` e pelos testes."""
    try:
        with open(caminho_log(), encoding="utf-8") as f:
            texto = f.read()
    except OSError:
        return ""
    partes = texto.split("=" * _LARGURA)
    return ("=" * _LARGURA + partes[-1]).strip() if len(partes) > 1 else texto.strip()
