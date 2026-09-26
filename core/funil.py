"""Funil de publicação por marketplace — onde cada oferta se perde.

Motivo de existir: a pergunta "por que saiu 1 oferta e não 3?" não tinha
resposta no log. O rastreador ML tinha um `contadores` que somava coisas
diferentes no mesmo balde (quarentena contava como duplicata, produto
rejeitado pelo anti-golpe contava como erro) e simplesmente NÃO contava
dois descartes — item sem título/link e item cortado pelo SCORE_MINIMO
saíam do loop com um `continue` mudo. O rastreador Amazon não tinha
contador nenhum. Então uma rodada que encontrava 20 produtos e publicava 1
não deixava registro de onde os outros 19 ficaram.

A regra aqui é conservação: toda oferta que entra tem que sair por
exatamente uma porta. `conferir()` prova isso a cada rodada — se a soma
das portas não bate com o que entrou, existe um `continue` sem contador em
algum lugar e o aviso aparece no log em vez de o número sumir em silêncio
(mesmo princípio da Regra 11: falha que não aparece é falha que volta).

Best-effort por contrato: nenhuma chamada daqui pode derrubar ou atrasar
uma publicação (Regra 6/13). Quem chama envolve em try/except; os métodos
que tocam disco ou rede já engolem a própria exceção e seguem.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

# Portas de saída, na ordem em que o rastreador as encontra. A ordem é a do
# relatório — é ela que mostra "morreu cedo" (scraping) vs "morreu tarde"
# (publicação), que pedem correções completamente diferentes.
ETAPAS: tuple[tuple[str, str], ...] = (
    ("incompletas",   "Dados incompletos (sem título ou link)"),
    ("duplicadas",    "Duplicadas (já publicadas)"),
    ("quarentena",    "Em quarentena (falhas anteriores)"),
    ("reivindicadas", "Reivindicadas por outro processo"),
    ("rejeitadas",    "Rejeitadas pelo anti-golpe"),
    ("score_baixo",   "Score abaixo do mínimo"),
    ("sem_afiliado",  "Sem link de afiliado válido"),
    ("erros",         "Erro ao processar"),
    ("falharam",      "Falharam ao publicar"),
    ("publicadas",    "Publicadas"),
    ("nao_avaliadas", "Não avaliadas (cota da rodada já cheia)"),
)

_NOMES = dict(ETAPAS)
_ARQUIVO = Path(os.getenv("FUNIL_ARQUIVO") or "data/funil.jsonl")
_TETO_LINHAS = 500          # mesmo teto do spool do n8n (Regra 13)
_lock = threading.Lock()


class Funil:
    """Contador de um marketplace numa rodada.

    Uso:
        funil = Funil("mercadolivre", meta=3)
        funil.encontradas(20)
        ...
        funil.marcar("duplicadas")
        ...
        funil.marcar("publicadas")
        funil.fechar(log)
    """

    def __init__(self, fonte: str, meta: int = 1) -> None:
        self.fonte = fonte
        self.meta = max(1, int(meta or 1))
        self.total_encontradas = 0
        self.etapas: dict[str, int] = {chave: 0 for chave, _ in ETAPAS}
        self.iniciado_em = datetime.now()

    # ── coleta ───────────────────────────────────────────────────────────
    def encontradas(self, quantas: int) -> None:
        """Soma o que o scraper devolveu (pode ser chamado por categoria)."""
        self.total_encontradas += max(0, int(quantas or 0))

    def marcar(self, etapa: str, quantas: int = 1) -> None:
        """Registra a saída de `quantas` ofertas por `etapa`.

        Etapa desconhecida vira `erros` em vez de KeyError: um contador
        errado não pode ser o que derruba uma rodada de publicação.
        """
        if etapa not in self.etapas:
            etapa = "erros"
        self.etapas[etapa] += max(0, int(quantas or 0))

    # ── leitura ──────────────────────────────────────────────────────────
    @property
    def publicadas(self) -> int:
        return self.etapas["publicadas"]

    def contabilizadas(self) -> int:
        """Quantas ofertas saíram por alguma porta conhecida."""
        return sum(self.etapas.values())

    def conferir(self) -> int:
        """Ofertas que entraram e não saíram por porta nenhuma.

        Zero é o esperado. Qualquer outro número é um `continue` sem
        contador no rastreador — o bug que este módulo existe para tornar
        visível. Negativo significa dupla contagem.
        """
        return self.total_encontradas - self.contabilizadas()

    def motivo_do_teto(self) -> str:
        """Em uma linha: por que publicou menos que a meta.

        É a resposta direta a "por que saíram 2 e não 3?" — nomeia a maior
        porta de perda em vez de deixar o número solto.
        """
        if self.publicadas >= self.meta:
            return f"meta de {self.meta} atingida"
        if self.total_encontradas == 0:
            return "o scraper não devolveu nenhuma oferta (falha de busca ou filtro cedo demais)"

        perdas = {
            chave: valor
            for chave, valor in self.etapas.items()
            if valor > 0 and chave not in ("publicadas", "nao_avaliadas")
        }
        if not perdas:
            return (f"candidatas insuficientes: {self.total_encontradas} encontrada(s) "
                    f"para uma meta de {self.meta}")

        maior = max(perdas, key=lambda c: perdas[c])
        detalhe = ", ".join(
            f"{perdas[c]} {_NOMES[c].lower()}"
            for c in sorted(perdas, key=lambda c: -perdas[c])[:3]
        )
        return (f"publicou {self.publicadas}/{self.meta} — maior perda: "
                f"{_NOMES[maior].lower()} ({self.etapas[maior]}); {detalhe}")

    def resumo(self) -> dict:
        return {
            "fonte": self.fonte,
            "meta": self.meta,
            "encontradas": self.total_encontradas,
            "publicadas": self.publicadas,
            "etapas": dict(self.etapas),
            "nao_contabilizadas": self.conferir(),
            "motivo": self.motivo_do_teto(),
            "duracao_s": round((datetime.now() - self.iniciado_em).total_seconds(), 1),
            "ts": datetime.now().isoformat(timespec="seconds"),
        }

    # ── saída ────────────────────────────────────────────────────────────
    def linhas(self) -> list[str]:
        """Tabela legível — é o que vai para o `data/bot.log` (Regra 9)."""
        saida = [
            f"📊 Funil {self.fonte} — {self.total_encontradas} encontrada(s), "
            f"{self.publicadas}/{self.meta} publicada(s)"
        ]
        for chave, rotulo in ETAPAS:
            valor = self.etapas[chave]
            if valor:
                saida.append(f"     {rotulo:.<44} {valor}")
        faltando = self.conferir()
        if faltando > 0:
            saida.append(
                f"     ⚠️  {faltando} oferta(s) sem porta de saída — há um descarte "
                f"sem contador no rastreador {self.fonte}"
            )
        elif faltando < 0:
            saida.append(
                f"     ⚠️  {-faltando} oferta(s) contadas 2x — contador duplicado "
                f"no rastreador {self.fonte}"
            )
        saida.append(f"     → {self.motivo_do_teto()}")
        return saida

    def fechar(self, log=None) -> dict:
        """Escreve o funil no log, no arquivo e no n8n. Nunca levanta."""
        dados = self.resumo()
        if log is not None:
            try:
                for linha in self.linhas():
                    log(linha)
            except Exception:
                pass
        _gravar(dados)
        _emitir(dados)
        return dados


# ── persistência ─────────────────────────────────────────────────────────
def _gravar(dados: dict) -> None:
    """Append em data/funil.jsonl, com teto de linhas. Best-effort."""
    try:
        with _lock:
            _ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
            with _ARQUIVO.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(dados, ensure_ascii=False) + "\n")
            _podar()
    except Exception:
        # Disco cheio ou arquivo travado não pode derrubar a rodada; o funil
        # já foi para o log, que é a via principal (Regra 9).
        pass


def _podar() -> None:
    try:
        linhas = _ARQUIVO.read_text(encoding="utf-8").splitlines()
        if len(linhas) > _TETO_LINHAS:
            _ARQUIVO.write_text(
                "\n".join(linhas[-_TETO_LINHAS:]) + "\n", encoding="utf-8"
            )
    except Exception:
        pass


def _emitir(dados: dict) -> None:
    try:
        from integrations import n8n  # noqa: PLC0415
        n8n.emitir("funil_rodada", dados)
    except Exception:
        pass


def ultimos(limite: int = 10) -> list[dict]:
    """Últimas rodadas registradas — alimenta /health e o relatório."""
    try:
        linhas = _ARQUIVO.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    saida = []
    for linha in linhas[-max(1, limite):]:
        try:
            saida.append(json.loads(linha))
        except Exception:
            continue
    return saida


def ultimo_por_fonte() -> dict[str, dict]:
    """Funil mais recente de cada marketplace, para o /health."""
    saida: dict[str, dict] = {}
    for registro in ultimos(limite=_TETO_LINHAS):
        fonte = registro.get("fonte")
        if fonte:
            saida[fonte] = registro
    return saida
