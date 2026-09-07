# -*- coding: utf-8 -*-
"""
Monitor de saúde: verifica conexão Telegram e fila de produtos.
Salva resultado em data/monitor_status.json e alerta no log se algo falhar.
Execute: python monitor.py
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

load_dotenv()

# monitor.py escreve seu próprio log e NÃO passa por core.error_logger.setup_logging,
# então o filtro de segredos de lá não o alcança. Foi por aqui que o token do
# Telegram foi parar em monitor.log — commitado num repositório público
# (achado em 2026-09-07): a exceção da python-telegram-bot diz
# "The token `<token>` was rejected by the server", e `str(e)` ia inteiro
# para o log e para data/monitor_status.json.
from core.segredos import FiltroDeSegredos, redigir

_STATUS_ARQUIVO = os.path.join("data", "monitor_status.json")

logging.basicConfig(
    filename="monitor.log",
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
for _h in logging.getLogger().handlers:
    _h.addFilter(FiltroDeSegredos())


async def _checar_telegram(token: str) -> dict:
    if not token:
        return {"ok": False, "erro": "TOKEN_TELEGRAM não definido"}
    try:
        from telegram import Bot
        async with Bot(token=token) as bot:
            me = await bot.get_me()
        return {"ok": True, "username": me.username}
    except Exception as e:
        return {"ok": False, "erro": redigir(str(e))}


def _checar_produtos() -> dict:
    try:
        with open("produtos.json", "r", encoding="utf-8") as f:
            dados = json.loads(f.read().strip() or "[]")
        pendentes = sum(1 for p in dados if p.get("status") != "enviado")
        return {"ok": True, "total": len(dados), "pendentes": pendentes}
    except FileNotFoundError:
        return {"ok": True, "total": 0, "pendentes": 0}
    except Exception as e:
        return {"ok": False, "erro": redigir(str(e))}


async def main() -> None:
    os.makedirs("data", exist_ok=True)
    token = os.getenv("TOKEN_TELEGRAM", "")

    status = {
        "verificado_em": datetime.now().isoformat(),
        "telegram": await _checar_telegram(token),
        "produtos": _checar_produtos(),
    }

    with open(_STATUS_ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    falhas = [k for k, v in status.items() if k != "verificado_em" and not v.get("ok")]
    for nome, resultado in status.items():
        if nome == "verificado_em":
            continue
        icone = "✅" if resultado.get("ok") else "❌"
        detalhe = resultado.get("username") or resultado.get("erro") or str(resultado)
        print(f"{icone} {nome}: {detalhe}")

    if falhas:
        logging.warning(f"Falhas detectadas: {falhas} — {status}")
        print(f"\n⚠️  {len(falhas)} falha(s). Veja monitor.log para detalhes.")
    else:
        print("\n✅ Todas as integrações operacionais.")


if __name__ == "__main__":
    asyncio.run(main())
