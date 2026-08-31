# -*- coding: utf-8 -*-
"""
DIAGNÓSTICO DO WHATSAPP — descobre qual elo da corrente está quebrado.

Entre a oferta ser publicada no Telegram e ela aparecer no grupo do WhatsApp
existem oito elos, e cada um registra em um log diferente (`data/bot.log`,
`data/whatsapp_queue_sender.log`, `data/errors.jsonl`). Quando o grupo fica
mudo, a pergunta "por quê?" exige cruzar os três à mão — e o sintoma é o
mesmo em quase todos os casos: nada acontece.

Este script percorre os elos NA ORDEM em que o envio depende deles e para no
primeiro que está quebrado, dizendo o que fazer. Não envia nada, não altera
nada: só lê.

    python diagnostico_whatsapp.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE, ".env"))
except Exception:
    pass

VERDE, VERM, AMAR, CINZA, FIM = "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m"
if os.name == "nt" and not os.getenv("WT_SESSION"):
    # O console clássico do Windows não interpreta ANSI; sem isso a saída sai
    # cheia de "[92m" e fica pior do que sem cor nenhuma.
    VERDE = VERM = AMAR = CINZA = FIM = ""

_problemas: list[str] = []


def ok(titulo: str, detalhe: str = "") -> None:
    print(f"  {VERDE}OK{FIM}    {titulo}" + (f" {CINZA}— {detalhe}{FIM}" if detalhe else ""))


def problema(titulo: str, o_que_fazer: str) -> None:
    print(f"  {VERM}FALHA{FIM} {titulo}")
    print(f"        {AMAR}→ {o_que_fazer}{FIM}")
    _problemas.append(titulo)


def aviso(titulo: str, detalhe: str = "") -> None:
    print(f"  {AMAR}nota{FIM}  {titulo}" + (f" {CINZA}— {detalhe}{FIM}" if detalhe else ""))


def secao(n: int, titulo: str) -> None:
    print(f"\n[{n}] {titulo}")


def main() -> int:
    print("=" * 66)
    print("  DIAGNOSTICO DO WHATSAPP — onde a corrente esta quebrada")
    print("=" * 66)

    # ── 1. Destino configurado ───────────────────────────────────────────
    secao(1, "Configuracao do destino")
    from integrations.whatsapp_sender import _group_id, wa_ativo  # noqa: PLC0415

    bruto = os.getenv("WHATSAPP_GROUP_ID", "")
    if not wa_ativo():
        motivo = ("nao esta no .env" if not bruto.strip()
                  else f"esta com o valor de exemplo ({bruto.strip()[:30]})")
        problema(
            f"WHATSAPP_GROUP_ID {motivo}",
            "preencha WHATSAPP_GROUP_ID no .env. Sem ele a oferta NEM ENTRA na fila: "
            "o rastreador so enfileira quando ha grupo configurado.",
        )
        # Sem destino, o resto da corrente nao chega a ser exercitado.
        print(f"\n{VERM}Este e o elo quebrado — os proximos nem chegam a ser usados.{FIM}")
        return 1
    ok("WHATSAPP_GROUP_ID preenchido", _group_id()[:28])

    nome = os.getenv("WHATSAPP_GROUP_NAME", "")
    if not nome:
        aviso(
            'WHATSAPP_GROUP_NAME nao esta no .env — usando o padrao "Bot-Ofertas"',
            "e por esse nome que a automacao acha a conversa (Ctrl+F). Se o seu "
            "grupo tem outro nome, a busca nao acha nada e o envio falha.",
        )
    else:
        ok("WHATSAPP_GROUP_NAME definido", nome)

    # ── 2. Pausa global ──────────────────────────────────────────────────
    secao(2, "Pausa global")
    from core import pausa  # noqa: PLC0415

    if pausa.pausado():
        info = pausa.info()
        problema(
            f"publicacao PAUSADA desde {info.get('pausado_em', '?')} ({info.get('motivo', '')})",
            f"para retomar: del \"{pausa.FLAG_PATH}\"  (ou o comando 'retomar' pelo n8n)",
        )
    else:
        ok("sem pausa ativa")

    # ── 3. Processo que esvazia a fila ───────────────────────────────────
    secao(3, "Processo whatsapp_queue_sender.py")
    try:
        import psutil  # noqa: PLC0415

        vivos = []
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                cl = " ".join(p.info.get("cmdline") or [])
                if "whatsapp_queue_sender.py" in cl:
                    vivos.append(p.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if vivos:
            ok("rodando", f"PID {', '.join(map(str, vivos))}")
            if len(vivos) > 1:
                problema(
                    f"{len(vivos)} instancias da fila rodando ao mesmo tempo",
                    "mate as extras: duas filas mandam a mesma oferta duas vezes",
                )
        else:
            problema(
                "nao esta rodando — a fila nunca e esvaziada",
                "suba o processo PAI (Regra 10): python -u startup.py",
            )
    except ImportError:
        aviso("psutil nao instalado — nao da para checar processos",
              "pip install -r requirements.txt")

    # ── 4. A fila ────────────────────────────────────────────────────────
    secao(4, "Fila de envio")
    import core.database as db  # noqa: PLC0415

    try:
        pendentes = db.tamanho_fila_whatsapp()
    except Exception as e:
        if "no such table" in str(e).lower():
            # A tabela nasce no db.inicializar(), chamado pelo startup.py e
            # pela propria fila. Nao existir significa que nenhum dos dois
            # rodou nesta pasta ainda — nao e um banco corrompido.
            problema(
                "a tabela da fila nem existe — o bot nunca rodou nesta pasta",
                "suba o bot uma vez: python -u startup.py (ele cria as tabelas)",
            )
        else:
            problema(f"nao consegui ler a fila: {e}", "confira data/ofertas.db")
        pendentes = -1

    if pendentes == 0:
        aviso("fila vazia",
              "normal logo apos um envio; se ficar sempre vazia, nenhuma oferta "
              "esta sendo enfileirada — veja o item 1 e o log do rastreador")
    elif pendentes > 0:
        ok(f"{pendentes} item(ns) esperando")
        prox = db.proximo_da_fila_whatsapp()
        if prox:
            _, criado_em, item = prox
            try:
                idade_min = (datetime.now() - datetime.fromisoformat(criado_em)).total_seconds() / 60
            except Exception:
                idade_min = -1
            titulo = (item.get("titulo") or "")[:45]
            if idade_min > 180:
                problema(
                    f"o mais antigo tem {idade_min:.0f} min e sera DESCARTADO (limite 3h): {titulo}",
                    "a fila enche mais rapido do que drena (1 envio a cada 30-45 min). "
                    "Item velho e descartado de proposito: preco/estoque ja mudaram.",
                )
            else:
                ok(f"proximo da fila: {titulo}", f"{idade_min:.0f} min na fila")

    # ── 5. Metodo de envio disponivel ────────────────────────────────────
    secao(5, "Como o envio sai (Evolution API ou WhatsApp Desktop)")
    try:
        from integrations.whatsapp_api import _configurada  # noqa: PLC0415

        if _configurada():
            from integrations.whatsapp_api import esta_conectada  # noqa: PLC0415

            if esta_conectada():
                ok("Evolution API configurada e conectada")
            else:
                problema("Evolution API configurada mas DESCONECTADA",
                         "reconecte a instancia ou limpe WHATSAPP_WEBHOOK_URL para usar o Desktop")
        else:
            print(f"  {CINZA}      Evolution API nao configurada — usando o WhatsApp Desktop{FIM}")
    except Exception as e:
        aviso(f"nao consegui checar a Evolution API: {str(e)[:60]}")

    if os.name != "nt":
        aviso("este nao e um Windows — o envio pelo WhatsApp Desktop so roda no PC do bot")
    else:
        try:
            from integrations.whatsapp_desktop import _processo_wa_rodando  # noqa: PLC0415

            if _processo_wa_rodando():
                ok("WhatsApp Desktop aberto")
            else:
                problema("WhatsApp Desktop FECHADO",
                         "abra o WhatsApp Desktop e deixe logado — e ele que envia")
        except Exception as e:
            aviso(f"nao consegui checar o WhatsApp Desktop: {str(e)[:60]}")

        # A automacao e por teclado/janela: sem estes pacotes ela nao roda.
        for pacote, para_que in (("pyautogui", "digitar e colar"),
                                 ("pygetwindow", "achar a janela"),
                                 ("PIL", "preparar a foto")):
            try:
                __import__(pacote)
                ok(f"{pacote} instalado", para_que)
            except ImportError:
                problema(f"{pacote} NAO instalado ({para_que})",
                         "pip install -r requirements.txt")

    # ── 6. O que o /health esta dizendo ──────────────────────────────────
    secao(6, "O que o /health responde")
    try:
        from core.healthcheck import _status_whatsapp  # noqa: PLC0415

        st = _status_whatsapp()
        if st.get("ok"):
            ok(f"/health: OK ({st.get('metodo')})", f"fila: {st.get('fila_pendente')}")
        else:
            problema(f"/health: OFF — {st.get('motivo')}", "veja os itens acima")
    except Exception as e:
        aviso(f"nao consegui montar o status: {str(e)[:60]}")

    # ── 7. Erros recentes do caminho do WhatsApp ─────────────────────────
    secao(7, "Erros recentes registrados (data/errors.jsonl)")
    try:
        from core.error_logger import erros_recentes  # noqa: PLC0415

        wa = [e for e in erros_recentes(200)
              if "wa" in str(e.get("operacao", "")).lower()
              or "whatsapp" in str(e.get("operacao", "")).lower()]
        if not wa:
            ok("nenhum erro do WhatsApp nos ultimos registros")
        else:
            for e in wa[-5:]:
                print(f"  {VERM}erro{FIM}  [{e.get('ts', '')[:16]}] {e.get('operacao')}: "
                      f"{str(e.get('mensagem'))[:70]}")
            _problemas.append("erros do WhatsApp em data/errors.jsonl")
    except Exception as e:
        aviso(f"nao consegui ler os erros: {str(e)[:60]}")

    # ── Resumo ───────────────────────────────────────────────────────────
    print("\n" + "=" * 66)
    if _problemas:
        print(f"{VERM}{len(_problemas)} problema(s) encontrado(s):{FIM}")
        for i, p in enumerate(_problemas, 1):
            print(f"  {i}. {p}")
        print("\nCorrija de cima para baixo: o primeiro costuma explicar os outros.")
        return 1

    print(f"{VERDE}Nenhum bloqueio encontrado na corrente.{FIM}")
    print("\nSe mesmo assim o grupo nao recebe, o proximo suspeito e o NOME do grupo:")
    print("  a automacao acha a conversa por Ctrl+F com WHATSAPP_GROUP_NAME, e o")
    print("  WhatsApp Desktop roda dentro de um WebView2 opaco — nao da para")
    print("  confirmar por codigo que a conversa certa abriu. Confira que o nome")
    print("  no .env e IGUAL ao que aparece na sua lista de conversas.")
    print("\nLembre tambem que apos cada reinicio o primeiro envio so sai em")
    print("30-45 min (intervalo aleatorio, de proposito, para nao parecer bot).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
