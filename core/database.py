# -*- coding: utf-8 -*-
"""
Camada de banco de dados SQLite.
Substitui os arquivos JSON (produtos.json, historico.json).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "bot_ofertas.db")

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS produtos (
    id                  TEXT PRIMARY KEY,
    titulo              TEXT NOT NULL,
    preco               REAL,
    preco_original      REAL,
    desconto_pct        REAL DEFAULT 0,
    foto                TEXT,
    categoria           TEXT DEFAULT 'geral',
    canal               TEXT DEFAULT 'geral',
    status              TEXT DEFAULT 'pendente',
    score               INTEGER DEFAULT 0,

    -- Afiliado
    affiliate_provider  TEXT,
    affiliate_link      TEXT,
    affiliate_status    TEXT DEFAULT 'pending',
    affiliate_created_at TEXT,

    -- Validação
    last_validation     TEXT,
    validation_ok       INTEGER DEFAULT 0,

    -- Rastreamento (futuro)
    clicks              INTEGER DEFAULT 0,
    commission_status   TEXT DEFAULT 'unknown',

    -- Conteúdo extra
    cupom               TEXT,

    -- Datas
    adicionado_em       TEXT NOT NULL,
    enviado_em          TEXT
);

CREATE TABLE IF NOT EXISTS execucoes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    iniciado_em             TEXT NOT NULL,
    concluido_em            TEXT,
    produtos_encontrados    INTEGER DEFAULT 0,
    links_gerados           INTEGER DEFAULT 0,
    links_falharam          INTEGER DEFAULT 0,
    publicados              INTEGER DEFAULT 0,
    duplicatas              INTEGER DEFAULT 0,
    erros                   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS erros_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo        TEXT,
    mensagem    TEXT,
    produto_id  TEXT,
    ocorrido_em TEXT
);

CREATE TABLE IF NOT EXISTS precos_historico (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    produto_id  TEXT NOT NULL,
    preco       REAL NOT NULL,
    visto_em    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_posts_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plataforma  TEXT NOT NULL,
    postado_em  TEXT NOT NULL
);

-- Fila de envio pro WhatsApp: publicar no Telegram fica imediato (como
-- sempre foi), mas o WhatsApp passa a esperar na fila e sai num intervalo
-- aleatório de 30-45min entre um post e outro (pedido do Daniel em
-- 2026-08-24, pra não parecer um bot postando nos dois lugares ao mesmo
-- tempo -- ver whatsapp_queue_sender.py).
CREATE TABLE IF NOT EXISTS fila_whatsapp (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    produto_json TEXT NOT NULL,
    criado_em    TEXT NOT NULL,
    enviado_em   TEXT
);

-- Falhas de publicação por produto (quarentena).
-- Antes desta tabela, um produto que falhasse ao publicar no Telegram
-- caía em db.liberar_claim() -> a linha 'processing' era apagada, o
-- produto voltava a ser raspado na rodada seguinte, falhava de novo, e
-- assim indefinidamente. Bug real observado com MLB68674214: 5 registros
-- de "telegram / falha ao publicar" pro MESMO produto entre 2026-08-25
-- 11:27 e 17:25 (relatório de problemas de 26/08), sem nenhum limite de
-- tentativas. Agora cada falha incrementa `tentativas`; ao atingir o
-- limite o produto entra em quarentena até `quarentena_ate` e deixa de
-- ser tentado, liberando a vaga da rodada pra uma oferta publicável.
CREATE TABLE IF NOT EXISTS falhas_publicacao (
    produto_id      TEXT PRIMARY KEY,
    titulo          TEXT DEFAULT '',
    tentativas      INTEGER NOT NULL DEFAULT 0,
    mensagem        TEXT DEFAULT '',
    primeira_falha  TEXT,
    ultima_falha    TEXT,
    quarentena_ate  TEXT
);

CREATE INDEX IF NOT EXISTS idx_falhas_quarentena ON falhas_publicacao(quarentena_ate);

CREATE INDEX IF NOT EXISTS idx_produtos_status ON produtos(status);
CREATE INDEX IF NOT EXISTS idx_produtos_adicionado ON produtos(adicionado_em);
CREATE INDEX IF NOT EXISTS idx_produtos_affiliate ON produtos(affiliate_status);
CREATE INDEX IF NOT EXISTS idx_precos_produto ON precos_historico(produto_id);
CREATE INDEX IF NOT EXISTS idx_social_posts_plataforma ON social_posts_log(plataforma, postado_em);

-- limpar_antigos() roda a cada ciclo (2 processos, a cada 30-75min) e
-- filtra exatamente por essas 3 colunas — sem índice, cada DELETE é um
-- full table scan repetido para sempre.
CREATE INDEX IF NOT EXISTS idx_erros_ocorrido ON erros_log(ocorrido_em);
CREATE INDEX IF NOT EXISTS idx_precos_visto ON precos_historico(visto_em);
CREATE INDEX IF NOT EXISTS idx_execucoes_iniciado ON execucoes(iniciado_em);
"""


def _ensure_dir():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)


@contextmanager
def _conn():
    _ensure_dir()
    con = sqlite3.connect(_DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def inicializar():
    """Cria as tabelas se não existirem."""
    _ensure_dir()
    with _conn() as con:
        con.executescript(_DDL)


# ── Produtos ──────────────────────────────────────────────────────────────────

_cupom_col_checked = False  # evita repetir PRAGMA table_info a cada inserir_produto()


def inserir_produto(p: dict) -> None:
    global _cupom_col_checked
    now = datetime.now().isoformat()
    with _conn() as con:
        # Adiciona coluna cupom se não existir (migração automática, 1x por processo)
        if not _cupom_col_checked:
            cols = {r[1] for r in con.execute("PRAGMA table_info(produtos)").fetchall()}
            if "cupom" not in cols:
                con.execute("ALTER TABLE produtos ADD COLUMN cupom TEXT")
            _cupom_col_checked = True
        con.execute("""
            INSERT OR IGNORE INTO produtos
                (id, titulo, preco, preco_original, desconto_pct, foto,
                 categoria, canal, status, score, cupom, adicionado_em)
            VALUES
                (:id, :titulo, :preco, :preco_original, :desconto_pct, :foto,
                 :categoria, :canal, :status, :score, :cupom, :adicionado_em)
        """, {
            "id":            p.get("id", f"p_{int(datetime.now().timestamp())}"),
            "titulo":        p.get("titulo", ""),
            "preco":         p.get("preco"),
            "preco_original": p.get("preco_original"),
            "desconto_pct":  p.get("desconto_pct", 0),
            "foto":          p.get("foto"),
            "categoria":     p.get("categoria", "geral"),
            "canal":         p.get("canal", "geral"),
            "status":        p.get("status", "pendente"),
            "score":         p.get("score", 0),
            "cupom":         p.get("cupom"),
            "adicionado_em": p.get("adicionado_em", now),
        })


def claim_produto(produto_id: str, titulo: str = "") -> bool:
    """Reivindica atomicamente um produto_id ANTES do trabalho lento de rede
    (geração de link de afiliado + publicação) começar. Retorna True se ESTE
    chamador conseguiu a reivindicação (pode prosseguir); False se outro
    processo já reivindicou o mesmo id (deve pular).

    Fecha a corrida entre processos que escaneiam a mesma categoria ao mesmo
    tempo (ex: rastreador.py e campanha_ferramentas.py ambos cobrem
    "ferramentas") — sem isso, os dois podiam passar por produto_id_existe()
    como False antes de qualquer um gravar, e publicar o mesmo produto 2x.
    INSERT OR IGNORE contra a PRIMARY KEY de produtos.id é atômico dentro de
    uma única transação SQLite — exatamente um chamador concorrente vence.

    Depois de reivindicar, o chamador deve usar atualizar_produto() (não
    inserir_produto()) para preencher os dados completos, já que a linha
    já existe."""
    now = datetime.now().isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO produtos (id, titulo, status, adicionado_em) "
            "VALUES (?, ?, 'processing', ?)",
            (produto_id, titulo, now),
        )
        return cur.rowcount == 1


def liberar_claim(produto_id: str) -> None:
    """Libera uma reivindicação feita via claim_produto() quando o trabalho
    subsequente falha (geração de link ou publicação) — remove a linha
    'processing' pra permitir nova tentativa na próxima rodada, em vez de
    bloquear o produto pra sempre com uma reivindicação órfã. O guard
    "AND status='processing'" torna a chamada seguro mesmo como rede de
    segurança genérica (ex: num handler de exceção) — se o item já foi
    atualizado pra 'enviado' por outro caminho, isso vira um no-op."""
    with _conn() as con:
        con.execute("DELETE FROM produtos WHERE id = ? AND status = 'processing'", (produto_id,))


def atualizar_produto(p: dict) -> None:
    """Preenche os dados completos de um produto já reivindicado via
    claim_produto() — usa UPDATE (não INSERT OR IGNORE) porque a linha já
    existe com um registro mínimo gravado no momento da reivindicação."""
    global _cupom_col_checked
    with _conn() as con:
        if not _cupom_col_checked:
            cols = {r[1] for r in con.execute("PRAGMA table_info(produtos)").fetchall()}
            if "cupom" not in cols:
                con.execute("ALTER TABLE produtos ADD COLUMN cupom TEXT")
            _cupom_col_checked = True
        con.execute("""
            UPDATE produtos
            SET titulo = :titulo, preco = :preco, preco_original = :preco_original,
                desconto_pct = :desconto_pct, foto = :foto, categoria = :categoria,
                canal = :canal, status = :status, score = :score, cupom = :cupom
            WHERE id = :id
        """, {
            "id":            p.get("id"),
            "titulo":        p.get("titulo", ""),
            "preco":         p.get("preco"),
            "preco_original": p.get("preco_original"),
            "desconto_pct":  p.get("desconto_pct", 0),
            "foto":          p.get("foto"),
            "categoria":     p.get("categoria", "geral"),
            "canal":         p.get("canal", "geral"),
            "status":        p.get("status", "pendente"),
            "score":         p.get("score", 0),
            "cupom":         p.get("cupom"),
        })


def atualizar_afiliado(produto_id: str, provider: str, link: str, status: str = "ok") -> None:
    now = datetime.now().isoformat()
    with _conn() as con:
        con.execute("""
            UPDATE produtos
            SET affiliate_provider  = ?,
                affiliate_link      = ?,
                affiliate_status    = ?,
                affiliate_created_at = ?
            WHERE id = ?
        """, (provider, link, status, now, produto_id))


def marcar_enviado(produto_id: str) -> None:
    now = datetime.now().isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE produtos SET status='enviado', enviado_em=? WHERE id=?",
            (now, produto_id)
        )


def marcar_duplicata(produto_id: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE produtos SET status='duplicata' WHERE id=?",
            (produto_id,)
        )


def listar_pendentes(limite: int = 50) -> list[dict]:
    with _conn() as con:
        rows = con.execute("""
            SELECT * FROM produtos
            WHERE status = 'pendente'
            ORDER BY score DESC
            LIMIT ?
        """, (limite,)).fetchall()
    return [dict(r) for r in rows]


def listar_todos(limite: int = 200) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM produtos ORDER BY adicionado_em DESC LIMIT ?", (limite,)
        ).fetchall()
    return [dict(r) for r in rows]


def link_ja_existe(link: str) -> bool:
    """Verifica se um link (ou URL base sem parâmetros) já está no banco.

    OBSOLETO para deduplicação — usa LIKE contra affiliate_link, que só
    funciona quando o link salvo é o fallback direto (contém a URL original).
    Quando o portal oficial de afiliados ML está logado, o link salvo vira
    um encurtado meli.la/XXXXX sem nenhuma relação textual com a URL
    original, e essa checagem nunca encontra o produto — permitindo posts
    duplicados reais. Use produto_id_existe(id) para deduplicação; mantido
    só por compatibilidade com chamadores antigos que ainda não migraram.
    """
    url_base = link.split("?")[0].split("#")[0].rstrip("/")
    with _conn() as con:
        row = con.execute("""
            SELECT id FROM produtos
            WHERE affiliate_link LIKE ? OR affiliate_link LIKE ?
            LIMIT 1
        """, (f"{url_base}%", f"%{url_base}%")).fetchone()
    return row is not None


def produto_id_existe(produto_id: str) -> bool:
    """Verifica deduplicação pelo ID estável do produto (slug da URL antes
    de virar link de afiliado) — correto independente do formato do link
    de afiliado salvo (direto com matt_tool= ou encurtado meli.la/XXXXX)."""
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM produtos WHERE id = ? LIMIT 1", (produto_id,)
        ).fetchone()
    return row is not None


def stats() -> dict:
    with _conn() as con:
        total       = con.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
        enviados    = con.execute("SELECT COUNT(*) FROM produtos WHERE status='enviado'").fetchone()[0]
        pendentes   = con.execute("SELECT COUNT(*) FROM produtos WHERE status='pendente'").fetchone()[0]
        duplicatas  = con.execute("SELECT COUNT(*) FROM produtos WHERE status='duplicata'").fetchone()[0]
        score_med   = con.execute("SELECT AVG(score) FROM produtos WHERE score > 0").fetchone()[0] or 0
        afil_ok     = con.execute("SELECT COUNT(*) FROM produtos WHERE affiliate_status='ok'").fetchone()[0]
        afil_fail   = con.execute("SELECT COUNT(*) FROM produtos WHERE affiliate_status='erro'").fetchone()[0]
        afil_pend   = con.execute("SELECT COUNT(*) FROM produtos WHERE affiliate_status='pending'").fetchone()[0]
        top = con.execute("""
            SELECT * FROM produtos WHERE score > 0
            ORDER BY score DESC LIMIT 10
        """).fetchall()
        ultimas = con.execute("""
            SELECT * FROM execucoes ORDER BY iniciado_em DESC LIMIT 5
        """).fetchall()
    return {
        "total":            total,
        "enviados":         enviados,
        "pendentes":        pendentes,
        "duplicatas":       duplicatas,
        "score_medio":      int(score_med),
        "afiliado_ok":      afil_ok,
        "afiliado_falha":   afil_fail,
        "afiliado_pendente": afil_pend,
        "taxa_afiliado":    round(afil_ok / max(afil_ok + afil_fail, 1) * 100, 1),
        "top_ofertas":      [dict(r) for r in top],
        "ultimas_execucoes": [dict(r) for r in ultimas],
    }


def erros_ultima_janela(minutos: int = 10) -> int:
    """Conta erros registrados em erros_log nos últimos `minutos` minutos.

    strftime(..., 'localtime', ...) em vez de datetime('now', ...): achado
    ao vivo em 2026-08-25 -- ocorrido_em é gravado em horário LOCAL
    (datetime.now().isoformat(), formato "T"), mas datetime('now', ...) do
    SQLite é UTC e usa separador " " (espaço). "2026-08-25 20:24" (UTC, com
    espaço) comparado como TEXTO contra "2026-08-25T11:27" (local, com "T")
    dá True pro ">=" só por causa do espaço ordenar antes do "T" no ASCII
    -- a janela de "N minutos" na prática pegava o dia inteiro. strftime
    com formato T + 'localtime' casa exatamente com o que é gravado.
    """
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM erros_log WHERE ocorrido_em >= "
            "strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', ?)",
            (f"-{minutos} minutes",),
        ).fetchone()
    return row[0]


# ── Limpeza automática ────────────────────────────────────────────────────────

def limpar_antigos(dias: int = 2, dias_precos: int = 35, dias_falhas: int = 7) -> int:
    """Remove produtos/erros/execuções com mais de `dias` dias, e histórico
    de preço com mais de `dias_precos` dias (janela separada e maior).

    Chamada automaticamente no início de cada execução do rastreador.
    Garante que o mesmo produto com oferta diferente possa ser repostado
    após o período definido, sem acúmulo de dados antigos.

    dias_precos > dias de propósito: core.price_alerts.queda_significativa()
    verifica queda de preço numa janela de 30 dias (dias=30, seu default),
    mas precos_historico era limpo junto com produtos em só 2 dias —
    a janela de 30 dias nunca tinha dado real pra olhar (média real
    confirmada: 1.77 pontos de histórico por produto, o mínimo pra
    detectar qualquer queda é 3). registrar_preco() já roda pra todo
    produto examinado, mesmo duplicata (rastreador.py, "registra mesmo
    se for duplicata") — o dado seria acumulado com o tempo se não fosse
    apagado cedo demais. 35 dias dá margem sobre a janela de 30 usada
    na consulta.
    """
    # strftime(..., 'localtime', ...) em vez de datetime('now', ...) --
    # mesmo motivo de erros_ultima_janela() acima (colunas gravadas em
    # horário local com separador "T", datetime('now') é UTC com espaço).
    # Pra janelas de DIAS o efeito prático é pequeno (poucas horas de
    # imprecisão), mas mantém consistência com o resto do arquivo.
    with _conn() as con:
        con.execute(
            "DELETE FROM produtos WHERE adicionado_em < "
            "strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', ?)",
            (f"-{dias} days",)
        )
        removidos = con.execute("SELECT changes()").fetchone()[0]
        con.execute(
            "DELETE FROM erros_log WHERE ocorrido_em < "
            "strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', ?)",
            (f"-{dias} days",)
        )
        con.execute(
            "DELETE FROM precos_historico WHERE visto_em < "
            "strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', ?)",
            (f"-{dias_precos} days",)
        )
        con.execute(
            "DELETE FROM execucoes WHERE iniciado_em < "
            "strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', ?)",
            (f"-{dias} days",)
        )
        # Falhas de publicação vivem mais que os produtos de propósito: a
        # linha em `produtos` é apagada em `dias` (2), então sem uma janela
        # maior aqui o produto problemático voltaria a ser raspado e
        # tentado logo depois de sair da quarentena de 24h, com o contador
        # zerado. `dias_falhas` cobre a quarentena inteira com folga.
        _garantir_tabela_falhas(con)
        con.execute(
            "DELETE FROM falhas_publicacao WHERE ultima_falha < "
            "strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime', ?)",
            (f"-{dias_falhas} days",)
        )
    return removidos


# ── Execuções ─────────────────────────────────────────────────────────────────

def iniciar_execucao() -> int:
    now = datetime.now().isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO execucoes (iniciado_em) VALUES (?)", (now,)
        )
    return cur.lastrowid


def finalizar_execucao(exec_id: int, **kwargs) -> None:
    now = datetime.now().isoformat()
    campos = ", ".join(f"{k}=?" for k in kwargs)
    valores = list(kwargs.values()) + [now, exec_id]
    with _conn() as con:
        con.execute(
            f"UPDATE execucoes SET {campos}, concluido_em=? WHERE id=?",
            valores
        )


def execucao_em_andamento(minutos_max: int = 20) -> bool:
    """True se existe uma execução iniciada e ainda não concluída dentro dos
    últimos `minutos_max` minutos — usado pelo desligamento agendado pra não
    matar o PC no meio de um ciclo de scraping/postagem. O corte por tempo
    existe porque iniciar_execucao()/finalizar_execucao() não são pareados
    via try/finally em rastreador.py — uma execução que crashou no meio fica
    com concluido_em NULL pra sempre, e sem o corte isso bloquearia o
    desligamento indefinidamente."""
    from datetime import timedelta
    corte = (datetime.now() - timedelta(minutes=minutos_max)).isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM execucoes WHERE concluido_em IS NULL AND iniciado_em >= ? LIMIT 1",
            (corte,),
        ).fetchone()
    return row is not None


def registrar_erro(tipo: str, mensagem: str, produto_id: str = "",
                   exc: BaseException | None = None) -> None:
    """Registra um erro na tabela `erros_log` e espelha no relatório do Desktop.

    `exc` existe por causa de um ponto cego real. Quando o chamador TEM uma
    exceção em mãos e passa só `str(e)`, o relatório perde arquivo, função,
    linha e traceback — e o erro vira uma linha solta impossível de
    investigar. Foi o que aconteceu com `campanha_ferramentas_falhou`: 19
    ocorrências entre 2026-08 e 2026-09, todas com a mensagem "Timed out" e
    NADA além disso, enquanto `amazon.rodada_falhou` — a mesma classe de
    falha, registrada por `log_erro()` — trazia traceback completo nas 43
    dela. Passando `exc`, os dois caminhos entregam o mesmo diagnóstico.
    """
    now = datetime.now().isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO erros_log (tipo, mensagem, produto_id, ocorrido_em) VALUES (?,?,?,?)",
            (tipo, mensagem, produto_id, now)
        )
    # Espelha no bloco de notas do Desktop + errors.jsonl -- sem isso essa
    # classe de erro (condição de negócio, sem exceção) fica invisível pra
    # quem só olha o arquivo do Desktop. Best-effort: nunca derruba o
    # registro no banco acima, que é a fonte de verdade.
    try:
        ctx = {"produto_id": produto_id} if produto_id else {}
        if exc is not None:
            # _nivel=2: pular este frame e apontar para quem capturou a
            # exceção de verdade.
            from core.error_logger import log_erro
            log_erro(tipo, exc, ctx, _nivel=2)
        else:
            from core.error_logger import registrar_evento
            registrar_evento(tipo, mensagem, ctx)
    except Exception:
        pass


# ── Falhas de publicação e quarentena ────────────────────────────────────────

MAX_TENTATIVAS_PUBLICACAO = 3
HORAS_QUARENTENA = 24

_falhas_tbl_checked = False


def _garantir_tabela_falhas(con) -> None:
    """Cria falhas_publicacao sob demanda.

    Não dá pra assumir que inicializar() rodou: whatsapp_queue_sender.py,
    gerar_relatorio_problemas.py e o healthcheck abrem o mesmo banco em
    processos separados, e um banco antigo (criado antes desta tabela
    existir) só ganha a tabela no próximo inicializar(). Um SELECT contra
    tabela inexistente levantaria OperationalError no meio do fluxo de
    publicação — exatamente o caminho que esta feature deveria proteger.
    """
    global _falhas_tbl_checked
    if _falhas_tbl_checked:
        return
    con.execute("""
        CREATE TABLE IF NOT EXISTS falhas_publicacao (
            produto_id      TEXT PRIMARY KEY,
            titulo          TEXT DEFAULT '',
            tentativas      INTEGER NOT NULL DEFAULT 0,
            mensagem        TEXT DEFAULT '',
            primeira_falha  TEXT,
            ultima_falha    TEXT,
            quarentena_ate  TEXT
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_falhas_quarentena "
        "ON falhas_publicacao(quarentena_ate)"
    )
    _falhas_tbl_checked = True


def registrar_falha_publicacao(
    produto_id: str,
    mensagem: str = "",
    titulo: str = "",
    max_tentativas: int = MAX_TENTATIVAS_PUBLICACAO,
    horas_quarentena: int = HORAS_QUARENTENA,
) -> dict:
    """Contabiliza mais uma falha de publicação do produto.

    Retorna {"produto_id", "tentativas", "quarentena": bool, "quarentena_ate"}.
    Quando `tentativas` atinge `max_tentativas`, o produto entra em
    quarentena por `horas_quarentena` e em_quarentena() passa a devolver
    True — o rastreador então pula esse produto em vez de tentar publicá-lo
    a cada rodada pra sempre.
    """
    from datetime import timedelta  # noqa: PLC0415
    agora = datetime.now()
    now_iso = agora.isoformat()
    with _conn() as con:
        _garantir_tabela_falhas(con)
        row = con.execute(
            "SELECT tentativas FROM falhas_publicacao WHERE produto_id = ?",
            (produto_id,),
        ).fetchone()
        tentativas = (row[0] if row else 0) + 1
        quarentena_ate = (
            (agora + timedelta(hours=horas_quarentena)).isoformat()
            if tentativas >= max_tentativas else None
        )
        if row:
            con.execute(
                "UPDATE falhas_publicacao SET tentativas=?, mensagem=?, "
                "ultima_falha=?, quarentena_ate=?, titulo=COALESCE(NULLIF(?,''), titulo) "
                "WHERE produto_id=?",
                (tentativas, str(mensagem)[:300], now_iso, quarentena_ate,
                 titulo, produto_id),
            )
        else:
            con.execute(
                "INSERT INTO falhas_publicacao (produto_id, titulo, tentativas, "
                "mensagem, primeira_falha, ultima_falha, quarentena_ate) "
                "VALUES (?,?,?,?,?,?,?)",
                (produto_id, titulo, tentativas, str(mensagem)[:300],
                 now_iso, now_iso, quarentena_ate),
            )
    return {
        "produto_id": produto_id,
        "titulo": titulo,
        "tentativas": tentativas,
        "max_tentativas": max_tentativas,
        "mensagem": str(mensagem)[:300],
        "quarentena": quarentena_ate is not None,
        "quarentena_ate": quarentena_ate,
    }


def em_quarentena(produto_id: str) -> bool:
    """True se o produto está em quarentena de publicação AGORA."""
    with _conn() as con:
        _garantir_tabela_falhas(con)
        row = con.execute(
            "SELECT quarentena_ate FROM falhas_publicacao WHERE produto_id = ?",
            (produto_id,),
        ).fetchone()
    if not row or not row[0]:
        return False
    try:
        return datetime.fromisoformat(row[0]) > datetime.now()
    except (TypeError, ValueError):
        return False


def limpar_falha_publicacao(produto_id: str) -> None:
    """Zera o histórico de falhas do produto — chamado após publicar com
    sucesso, pra que uma falha isolada de ontem não conte pro limite de hoje."""
    with _conn() as con:
        _garantir_tabela_falhas(con)
        con.execute("DELETE FROM falhas_publicacao WHERE produto_id = ?", (produto_id,))


def listar_quarentena(limite: int = 50, apenas_ativas: bool = True) -> list[dict]:
    """Produtos que falharam ao publicar (para /quarentena e para o n8n)."""
    sql = "SELECT * FROM falhas_publicacao"
    params: list = []
    if apenas_ativas:
        sql += " WHERE quarentena_ate IS NOT NULL AND quarentena_ate > ?"
        params.append(datetime.now().isoformat())
    sql += " ORDER BY ultima_falha DESC LIMIT ?"
    params.append(limite)
    with _conn() as con:
        _garantir_tabela_falhas(con)
        rows = con.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def liberar_quarentena(produto_id: str = "") -> int:
    """Tira produto(s) da quarentena (comando manual/n8n). Sem produto_id,
    libera todos. Retorna quantas linhas foram liberadas."""
    with _conn() as con:
        _garantir_tabela_falhas(con)
        if produto_id:
            con.execute("DELETE FROM falhas_publicacao WHERE produto_id = ?", (produto_id,))
        else:
            con.execute("DELETE FROM falhas_publicacao")
        return con.execute("SELECT changes()").fetchone()[0]


# ── Fila de envio WhatsApp (intervalo aleatório 30-45min) ─────────────────────

def enfileirar_whatsapp(item: dict) -> None:
    """Coloca um produto na fila do WhatsApp, pra ser enviado depois pelo
    whatsapp_queue_sender.py num intervalo aleatório -- não imediatamente
    junto com o Telegram."""
    import json
    with _conn() as con:
        con.execute(
            "INSERT INTO fila_whatsapp (produto_json, criado_em) VALUES (?,?)",
            (json.dumps(item, ensure_ascii=False), datetime.now().isoformat()),
        )


def proximo_da_fila_whatsapp() -> tuple[int, str, dict] | None:
    """Retorna (id, criado_em, produto) do item mais antigo ainda não
    enviado, ou None se a fila estiver vazia."""
    import json
    with _conn() as con:
        row = con.execute(
            "SELECT id, criado_em, produto_json FROM fila_whatsapp WHERE enviado_em IS NULL "
            "ORDER BY criado_em ASC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return row[0], row[1], json.loads(row[2])


def marcar_fila_whatsapp_enviado(fila_id: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE fila_whatsapp SET enviado_em = ? WHERE id = ?",
            (datetime.now().isoformat(), fila_id),
        )


def tamanho_fila_whatsapp() -> int:
    with _conn() as con:
        return con.execute(
            "SELECT COUNT(*) FROM fila_whatsapp WHERE enviado_em IS NULL"
        ).fetchone()[0]


# ── Histórico de preço ────────────────────────────────────────────────────────

def registrar_preco(produto_id: str, preco: float | None) -> None:
    """Registra um ponto de preço no histórico (1x por produto por dia).

    Usado para detectar preço inflado e exibir 'menor preço em X dias'.
    Evita duplicar registros do mesmo dia para o mesmo produto.
    """
    if not produto_id or not preco or preco <= 0:
        return
    now = datetime.now()
    hoje = now.date().isoformat()
    with _conn() as con:
        ja_hoje = con.execute(
            "SELECT 1 FROM precos_historico WHERE produto_id=? AND substr(visto_em,1,10)=? LIMIT 1",
            (produto_id, hoje),
        ).fetchone()
        if ja_hoje:
            return
        con.execute(
            "INSERT INTO precos_historico (produto_id, preco, visto_em) VALUES (?,?,?)",
            (produto_id, float(preco), now.isoformat()),
        )


def historico_preco(produto_id: str, dias: int = 30) -> dict:
    """Retorna estatísticas de preço dos últimos N dias para um produto.

    Returns:
        dict com: menor, maior, atual (último registrado), pontos (qtd de leituras),
        e_menor_periodo (bool — preço atual é o menor do período).
        Campos ausentes/None se não houver histórico.
    """
    from datetime import timedelta
    corte = (datetime.now() - timedelta(days=dias)).isoformat()
    with _conn() as con:
        rows = con.execute(
            "SELECT preco, visto_em FROM precos_historico "
            "WHERE produto_id=? AND visto_em >= ? ORDER BY visto_em",
            (produto_id, corte),
        ).fetchall()
    if not rows:
        return {"menor": None, "maior": None, "atual": None,
                "pontos": 0, "e_menor_periodo": False, "dias": dias}
    precos = [r["preco"] for r in rows]
    atual = precos[-1]
    menor = min(precos)
    return {
        "menor":           round(menor, 2),
        "maior":           round(max(precos), 2),
        "atual":           round(atual, 2),
        "pontos":          len(precos),
        "e_menor_periodo": atual <= menor + 0.01 and len(precos) >= 2,
        "dias":            dias,
    }


# ── Horário de postagem em redes sociais ────────────────────────────────────
# rastreador.py e rastreador_amazon.py rodam como processos separados e não
# compartilham memória — por isso o controle de "último post"/"posts hoje"
# fica aqui no SQLite compartilhado (visto por ambos via WAL), em vez de um
# dict em módulo que só veria a metade dos posts reais. Usado por
# core/posting_schedule.py.

def registrar_post_social(plataforma: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO social_posts_log (plataforma, postado_em) VALUES (?, ?)",
            (plataforma, datetime.now().isoformat()),
        )


def ultimo_post_social(plataforma: str) -> str | None:
    with _conn() as con:
        row = con.execute(
            "SELECT MAX(postado_em) FROM social_posts_log WHERE plataforma = ?",
            (plataforma,),
        ).fetchone()
        return row[0] if row and row[0] else None


def contar_posts_social_desde(plataforma: str, desde_iso: str) -> int:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM social_posts_log WHERE plataforma = ? AND postado_em >= ?",
            (plataforma, desde_iso),
        ).fetchone()
        return int(row[0]) if row and row[0] else 0


# ── Rastreamento de cliques ──────────────────────────────────────────────────
# A coluna `clicks` já existia no schema (marcada "Rastreamento (futuro)") mas
# nunca foi escrita por nenhum código — essas duas funções são o primeiro uso
# real dela, via web/app.py:/r/<produto_id>.

def registrar_clique(produto_id: str) -> str | None:
    """Incrementa o contador de cliques do produto e retorna o affiliate_link
    real pra redirecionar. None se o produto não existe."""
    with _conn() as con:
        con.execute(
            "UPDATE produtos SET clicks = clicks + 1 WHERE id = ?",
            (produto_id,),
        )
        row = con.execute(
            "SELECT affiliate_link FROM produtos WHERE id = ?",
            (produto_id,),
        ).fetchone()
        return row[0] if row and row[0] else None


def top_clicados(limite: int = 20) -> list[dict]:
    """Produtos mais clicados, para relatório."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT id, titulo, categoria, affiliate_provider, clicks, enviado_em
            FROM produtos
            WHERE clicks > 0
            ORDER BY clicks DESC
            LIMIT ?
            """,
            (limite,),
        ).fetchall()
        return [dict(r) for r in rows]
