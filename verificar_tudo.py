# -*- coding: utf-8 -*-
"""
VERIFICAR TUDO — checagem sob demanda, de ponta a ponta, do bot_ofertas.

Diferente do `verificacao_diaria.py`, que roda às 01:00 e manda um resumo por
Telegram: este aqui é para você rodar QUANDO QUISER e ver, item a item, o que
está de pé e o que não está.

O que ele NUNCA faz:
  - não publica nada, em canal nenhum (nem Telegram, nem WhatsApp);
  - não imprime valor de segredo. Do .env mostra só o NOME da chave e se está
    preenchida — ver Regra 10 e o vazamento de 2026-09-07;
  - não reinicia, não para e não mexe em configuração de nada.

Uso:
    python verificar_tudo.py              # tudo, menos a raspagem (rápido)
    python verificar_tudo.py --raspagem   # inclui raspar ML e Amazon de verdade
    python verificar_tudo.py --sem-rede   # nem Telegram, só o que é local

Sai com código 0 se tudo passou, 1 se algo falhou — dá para usar em tarefa
agendada.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

VERDE, VERM, AMAR, CINZA, FIM = "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m"
if os.name == "nt" and not os.getenv("WT_SESSION"):
    VERDE = VERM = AMAR = CINZA = FIM = ""

_resultados: list[tuple[str, str, str]] = []   # (estado, item, detalhe)


def ok(item, detalhe=""):      _reg("OK", item, detalhe)
def falha(item, detalhe=""):   _reg("FALHA", item, detalhe)
def aviso(item, detalhe=""):   _reg("AVISO", item, detalhe)
def pulado(item, detalhe=""):  _reg("PULADO", item, detalhe)


def _reg(estado, item, detalhe):
    _resultados.append((estado, item, detalhe))
    cor = {"OK": VERDE, "FALHA": VERM, "AVISO": AMAR, "PULADO": CINZA}[estado]
    print(f"  {cor}{estado:<6}{FIM} {item}" + (f"  {CINZA}{detalhe}{FIM}" if detalhe else ""))


def titulo(t):
    print(f"\n{t}")
    print("-" * min(len(t), 70))


# ─────────────────────────────────────────────────────────── 1. ambiente
def bloco_ambiente():
    titulo("1. Ambiente")
    ok("Python", f"{sys.version.split()[0]} em {sys.executable}")

    # Estes cinco sao os que o bot precisa para funcionar inteiro. O
    # win32clipboard so existe no Windows e e de quem depende a FOTO do
    # WhatsApp — sem ele a Regra 5 aborta o envio e o grupo fica mudo.
    pacotes = [("telegram", "publicar no Telegram"),
               ("playwright", "raspar ML e Amazon"),
               ("dotenv", "ler o .env"),
               ("psutil", "ver se os processos estao vivos"),
               ("httpx", "chamadas HTTP")]
    if sys.platform == "win32":
        pacotes.append(("win32clipboard", "anexar a foto no WhatsApp"))

    for mod, para_que in pacotes:
        try:
            importlib.import_module(mod)
            ok(f"pacote {mod}", para_que)
        except Exception as e:
            falha(f"pacote {mod}", f"{para_que} — {type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────── 2. .env
def bloco_env():
    titulo("2. Configuracao (.env — so os nomes, nunca o valor)")
    caminho = os.path.join(BASE, ".env")
    if not os.path.exists(caminho):
        falha(".env", "nao encontrado em " + BASE)
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(caminho)
    except Exception:
        pass

    obrigatorias = ["TOKEN_TELEGRAM", "CANAL_GERAL"]
    importantes = ["ML_AFFILIATE_TOOL_ID", "AMAZON_AFFILIATE_TAG",
                   "WHATSAPP_GROUP_ID", "WHATSAPP_GROUP_NAME"]
    opcionais = ["ANTHROPIC_API_KEY", "N8N_WEBHOOK_URL", "PAPEL",
                 "HORA_LIGAR", "HORA_DESLIGAR"]

    for chave in obrigatorias:
        v = (os.getenv(chave) or "").strip()
        (ok if v else falha)(chave, f"{len(v)} caracteres" if v else "VAZIO — o bot nao publica sem isso")
    for chave in importantes:
        v = (os.getenv(chave) or "").strip()
        if v:
            ok(chave, f"{len(v)} caracteres")
        elif chave == "WHATSAPP_GROUP_NAME":
            aviso(chave, "vazio — a busca vai procurar o literal 'Bot-Ofertas'")
        else:
            aviso(chave, "vazio — essa loja/canal fica desligada")
    for chave in opcionais:
        v = (os.getenv(chave) or "").strip()
        (ok if v else pulado)(chave, f"{len(v)} caracteres" if v else "nao definida (opcional)")


# ────────────────────────────────────────────── 3. links de afiliado
def bloco_afiliado():
    titulo("3. Link de afiliado (validado com parse_qs, nunca por substring)")
    # Regra 3/4/11: o parametro tem de chegar como QUERY DE VERDADE. Conferir
    # por substring foi o bug de 2026-08-04 — o matt_tool ficava preso dentro
    # de um #fragment e a checagem antiga aprovava.
    try:
        from affiliates.mercadolivre import MLAffiliateProvider
        prov = MLAffiliateProvider()
        url = "https://www.mercadolivre.com.br/produto-teste/p/MLB123#polycard_client=x&y=z"
        link = prov._link_direto_com_afiliado(url)
        q = parse_qs(urlsplit(link).query)
        if q.get("matt_tool", [None])[0] == "47114387" and not urlsplit(link).fragment:
            ok("Mercado Livre", "matt_tool=47114387 como query real, #fragment removido")
        else:
            falha("Mercado Livre", f"matt_tool nao chegou como query: {link[:90]}")
    except Exception as e:
        falha("Mercado Livre", f"{type(e).__name__}: {e}")

    try:
        tag = (os.getenv("AMAZON_AFFILIATE_TAG") or "").strip()
        if not tag:
            pulado("Amazon", "AMAZON_AFFILIATE_TAG vazia")
        else:
            from integrations.amazon_scraper import _link_afiliado
            link = _link_afiliado("https://www.amazon.com.br/dp/B0TESTE123?ref=x#frag")
            q = parse_qs(urlsplit(link).query)
            if q.get("tag", [None])[0] == tag and not urlsplit(link).fragment:
                ok("Amazon", "tag como query real, #fragment removido")
            else:
                falha("Amazon", f"tag nao chegou como query: {link[:90]}")
    except Exception as e:
        falha("Amazon", f"{type(e).__name__}: {e}")

    # Regra 14: a troca de origem por canal nao pode comer o afiliado.
    try:
        from core.tracking import marcar_origem, afiliado_intacto
        base = "https://www.mercadolivre.com.br/x/p/MLB1?matt_tool=47114387"
        for canal in ("telegram", "whatsapp"):
            marcado = marcar_origem(base, canal)
            if afiliado_intacto(marcado, matt_tool="47114387"):
                ok(f"origem {canal}", parse_qs(urlsplit(marcado).query).get("matt_source", [""])[0])
            else:
                falha(f"origem {canal}", "a marcacao de canal comeu o matt_tool")
    except Exception as e:
        falha("marcacao de origem", f"{type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────── 4. banco
def bloco_banco():
    titulo("4. Banco de dados")
    caminho = os.path.join(BASE, "data", "bot_ofertas.db")
    if not os.path.exists(caminho):
        falha("data/bot_ofertas.db", "nao existe — o bot nunca rodou nesta pasta?")
        return
    try:
        con = sqlite3.connect(caminho)
        tabelas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("produtos", "fila_whatsapp"):
            (ok if t in tabelas else falha)(f"tabela {t}", "presente" if t in tabelas else "AUSENTE")

        if "produtos" in tabelas:
            n = con.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
            ok("produtos no banco", str(n))
        if "fila_whatsapp" in tabelas:
            pend = con.execute("SELECT COUNT(*) FROM fila_whatsapp WHERE enviado_em IS NULL").fetchone()[0]
            env = con.execute("SELECT COUNT(*) FROM fila_whatsapp WHERE enviado_em IS NOT NULL").fetchone()[0]
            # Fila grande + nada enviado = ninguem esta drenando (Regra 13).
            if pend > 50 and env == 0:
                falha("fila do WhatsApp", f"{pend} pendentes e 0 enviados — ninguem esta drenando")
            elif pend > 50:
                aviso("fila do WhatsApp", f"{pend} pendentes, {env} enviados — drenando devagar")
            else:
                ok("fila do WhatsApp", f"{pend} pendentes, {env} enviados")
        con.close()
    except Exception as e:
        falha("banco", f"{type(e).__name__}: {e}")

    try:
        from core import database as db
        q = db.listar_quarentena() if hasattr(db, "listar_quarentena") else []
        (aviso if q else ok)("quarentena", f"{len(q)} produto(s)" if q else "vazia")
    except Exception:
        pulado("quarentena", "nao consegui consultar")


# ─────────────────────────────────────────────────────── 5. processos
def bloco_processos():
    titulo("5. Processos (o bot esta de pe?)")
    try:
        import psutil
    except ImportError:
        pulado("processos", "psutil ausente — sem ele NAO da para saber, e 'nao sei' != 'parado'")
        return

    alvos = {"startup.py": "processo pai",
             "rastreador.py": "Mercado Livre",
             "rastreador_amazon.py": "Amazon",
             "campanha_ferramentas.py": "ferramentas",
             "whatsapp_queue_sender.py": "drenador da fila do WhatsApp"}
    vivos = {k: [] for k in alvos}
    for p in psutil.process_iter(["pid", "cmdline"]):
        linha = " ".join(p.info.get("cmdline") or [])
        for alvo in alvos:
            if alvo in linha:
                vivos[alvo].append(p.info["pid"])
    for alvo, para_que in alvos.items():
        pids = vivos[alvo]
        if pids:
            ok(alvo, f"{para_que} — PID {', '.join(map(str, pids))}")
        else:
            falha(alvo, f"{para_que} — NAO esta rodando")

    if sys.platform == "win32":
        wa = [p.info["name"] for p in psutil.process_iter(["name"])
              if "whatsapp" in (p.info.get("name") or "").lower()]
        (ok if wa else falha)("WhatsApp Desktop", ", ".join(sorted(set(wa))) if wa else "fechado — sem ele nao ha envio")


# ──────────────────────────────────────────────────────── 6. Telegram
def bloco_telegram():
    titulo("6. Telegram (so consulta a identidade do bot — nao publica)")
    token = (os.getenv("TOKEN_TELEGRAM") or "").strip()
    if not token:
        falha("getMe", "TOKEN_TELEGRAM vazio")
        return
    try:
        import httpx
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
        if r.status_code == 200 and r.json().get("ok"):
            u = r.json()["result"]
            ok("getMe", f"@{u.get('username')} (id {u.get('id')})")
        elif r.status_code == 401:
            falha("getMe", "401 — token invalido ou revogado")
        else:
            falha("getMe", f"HTTP {r.status_code}")
    except Exception as e:
        falha("getMe", f"{type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────── 7. raspagem
def bloco_raspagem():
    titulo("7. Raspagem ao vivo (lenta — so com --raspagem)")
    import asyncio

    try:
        # Mesma funcao que o rastreador.py usa (linha 38/145) — testar outra
        # provaria o caminho errado.
        from integrations.ml_browser import buscar_ofertas_browser_async
        t0 = time.time()
        itens = asyncio.run(buscar_ofertas_browser_async("eletronicos", desconto_min=20))
        seg = time.time() - t0
        (ok if itens else falha)("Mercado Livre", f"{len(itens)} produto(s) em {seg:.0f}s"
                                 + ("" if itens else " — extrator provavelmente quebrado"))
    except Exception as e:
        falha("Mercado Livre", f"{type(e).__name__}: {e}")

    try:
        from integrations.amazon_scraper import buscar_cupons_amazon_async, amazon_ativo
        if not amazon_ativo():
            pulado("Amazon", "AMAZON_AFFILIATE_TAG vazia")
        else:
            t0 = time.time()
            itens = asyncio.run(buscar_cupons_amazon_async(desconto_min=10, limite=5))
            seg = time.time() - t0
            com_cupom = sum(1 for p in itens if p.get("cupom"))
            if not itens:
                falha("Amazon", f"0 produto(s) em {seg:.0f}s — extrator quebrado (visto em 17/09)")
            elif com_cupom == 0:
                aviso("Amazon", f"{len(itens)} produto(s), NENHUM com cupom — badge pode ter mudado")
            else:
                ok("Amazon", f"{len(itens)} produto(s), {com_cupom} com cupom, {seg:.0f}s")
    except Exception as e:
        falha("Amazon", f"{type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────── 8. testes
def bloco_testes():
    titulo("8. Suite de testes do projeto")
    pasta = os.path.join(BASE, "tests")
    if not os.path.isdir(pasta):
        pulado("testes", "pasta tests/ ausente")
        return
    for arq in sorted(f for f in os.listdir(pasta) if f.startswith("test_") and f.endswith(".py")):
        try:
            r = subprocess.run([sys.executable, os.path.join(pasta, arq)],
                               cwd=BASE, capture_output=True, text=True, timeout=300)
            # A ultima linha as vezes e so a moldura de "=" do relatorio;
            # procura de tras pra frente a que realmente tem o placar.
            linhas = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
            resumo = next((l for l in reversed(linhas)
                           if any(m in l for m in ("passaram", "VALIDADO", "/"))),
                          linhas[-1] if linhas else "(sem saida)")
            (ok if r.returncode == 0 else falha)(arq, resumo[:80])
        except subprocess.TimeoutExpired:
            falha(arq, "passou de 5 min")
        except Exception as e:
            falha(arq, f"{type(e).__name__}: {e}")


# ───────────────────────────────────────────────────────────── main
def main() -> int:
    com_rede = "--sem-rede" not in sys.argv
    com_raspagem = "--raspagem" in sys.argv

    print("=" * 70)
    print("  VERIFICACAO COMPLETA — bot_ofertas")
    print(f"  pasta: {BASE}")
    print("=" * 70)

    bloco_ambiente()
    bloco_env()
    bloco_afiliado()
    bloco_banco()
    bloco_processos()
    if com_rede:
        bloco_telegram()
    else:
        titulo("6. Telegram"); pulado("getMe", "--sem-rede")
    if com_raspagem and com_rede:
        bloco_raspagem()
    else:
        titulo("7. Raspagem ao vivo"); pulado("ML e Amazon", "rode com --raspagem para incluir (leva minutos)")
    bloco_testes()

    falhas = [r for r in _resultados if r[0] == "FALHA"]
    avisos = [r for r in _resultados if r[0] == "AVISO"]
    print("\n" + "=" * 70)
    print(f"  {len(_resultados) - len(falhas) - len(avisos)} OK   |   {len(avisos)} aviso(s)   |   {len(falhas)} falha(s)")
    print("=" * 70)
    if falhas:
        print(f"\n{VERM}O que esta quebrado:{FIM}")
        for _, item, detalhe in falhas:
            print(f"  - {item}: {detalhe}")
    if avisos:
        print(f"\n{AMAR}Atencao (nao impede de funcionar):{FIM}")
        for _, item, detalhe in avisos:
            print(f"  - {item}: {detalhe}")
    if not falhas:
        print(f"\n{VERDE}Tudo o que foi verificado esta funcionando.{FIM}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
