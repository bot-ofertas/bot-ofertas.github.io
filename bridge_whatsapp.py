# -*- coding: utf-8 -*-
"""
BRIDGE TELEGRAM → WHATSAPP
===========================
Monitora o canal Telegram via API direta (sem polling library) e envia
cada nova oferta automaticamente para o grupo WhatsApp via pyautogui.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import asyncio
import logging
import os
import re
import time
import requests

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BRIDGE] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/bridge.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

TOKEN    = os.getenv("TOKEN_TELEGRAM", "")
CANAL_ID = os.getenv("CANAL_GERAL", "")
GROUP_ID = os.getenv("WHATSAPP_GROUP_ID", "")
API_URL  = f"https://api.telegram.org/bot{TOKEN}"

_processados: set[int] = set()


def _get_updates(offset: int = 0) -> list[dict]:
    try:
        r = requests.get(
            f"{API_URL}/getUpdates",
            params={"offset": offset, "timeout": 20,
                    "allowed_updates": ["channel_post", "message"]},
            timeout=25,
        )
        data = r.json()
        if data.get("ok"):
            return data.get("result", [])
    except Exception as e:
        log.warning("getUpdates falhou: %s", e)
    return []


def _extrair_produto(texto: str) -> dict | None:
    if not texto or len(texto) < 20:
        return None
    if not re.search(r"(meli\.la|mercadolivre\.com|amazon\.com\.br|amzn\.to)", texto, re.I):
        return None

    link = ""
    m = re.search(r"https?://\S+", texto)
    link = m.group(0) if m else ""

    linhas = [l.strip() for l in texto.split("\n") if l.strip()]
    titulo = ""
    for linha in linhas:
        limpa = re.sub(r"^[🔥💰🏷️✅➡️#\s*<>b/]+", "", linha).strip()
        if len(limpa) > 10:
            titulo = limpa[:80]
            break

    preco = None
    m2 = re.search(r"R\$\s*([\d.,]+)", texto)
    if m2:
        try:
            preco = float(m2.group(1).replace(".", "").replace(",", "."))
        except Exception:
            pass

    desconto_pct = 0
    m3 = re.search(r"(\d+)%\s*OFF", texto, re.I)
    if m3:
        desconto_pct = int(m3.group(1))

    return {
        "titulo": titulo or "Oferta especial",
        "link": link,
        "preco": preco,
        "desconto_pct": desconto_pct,
        "categoria": "geral",
    }


def _gerar_mensagem_wa(produto: dict, texto_original: str) -> str:
    try:
        from core.ai_content import gerar_conteudo
        c = gerar_conteudo(produto)
        if c.get("mensagem_whatsapp"):
            return c["mensagem_whatsapp"]
    except Exception:
        pass
    return texto_original[:500]


def _enviar_whatsapp(mensagem: str) -> bool:
    try:
        import pygetwindow as gw
        import pyautogui
        import pyperclip
    except ImportError:
        log.error("Falta: pip install pyautogui pygetwindow pyperclip")
        return False

    janelas = gw.getWindowsWithTitle("WhatsApp")
    if not janelas:
        log.error("WhatsApp Web não encontrado no Chrome.")
        return False

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.4

    try:
        janelas[0].activate()
        time.sleep(1.5)

        pyautogui.hotkey("ctrl", "alt", "/")
        time.sleep(0.8)

        pyautogui.typewrite("Bot-Ofertas", interval=0.07)
        time.sleep(1.5)

        pyautogui.press("down")
        time.sleep(0.3)
        pyautogui.press("enter")
        time.sleep(2.0)

        pyperclip.copy(mensagem)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)
        pyautogui.press("enter")
        time.sleep(0.5)

        log.info("✅ WhatsApp: mensagem enviada no grupo")
        return True
    except Exception as e:
        log.error("pyautogui falhou: %s", e)
        return False


def main():
    if not TOKEN:
        print("❌ TOKEN_TELEGRAM não configurado no .env")
        return

    print("=" * 55)
    print("🌉 BRIDGE TELEGRAM → WHATSAPP")
    print("=" * 55)
    print(f"📡 Canal: {CANAL_ID or 'todos'}")
    print(f"💚 Grupo WhatsApp: {GROUP_ID or 'não configurado'}")
    print("Ctrl+C para parar.\n")

    offset = 0
    while True:
        updates = _get_updates(offset)

        for upd in updates:
            update_id = upd.get("update_id", 0)
            offset = max(offset, update_id + 1)

            msg = upd.get("channel_post") or upd.get("message")
            if not msg:
                continue

            msg_id = msg.get("message_id", 0)
            if msg_id in _processados:
                continue

            chat_id = str(msg.get("chat", {}).get("id", ""))
            if CANAL_ID and chat_id != str(CANAL_ID):
                continue

            texto = msg.get("text") or msg.get("caption") or ""
            produto = _extrair_produto(texto)
            if not produto:
                continue

            _processados.add(msg_id)
            log.info("Nova oferta detectada — msg #%d: %s", msg_id, produto["titulo"][:50])

            mensagem_wa = _gerar_mensagem_wa(produto, texto)
            _enviar_whatsapp(mensagem_wa)

        if not updates:
            time.sleep(5)


if __name__ == "__main__":
    main()
