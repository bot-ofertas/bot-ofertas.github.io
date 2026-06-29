# -*- coding: utf-8 -*-
"""
BRIDGE TELEGRAM → WHATSAPP
===========================
Monitora o canal Telegram em tempo real e envia cada nova oferta
automaticamente para o grupo WhatsApp, usando IA para gerar o conteúdo.

Pré-requisitos:
  - WhatsApp Web aberto no Chrome (aba ativa com sessão logada)
  - .env configurado com TOKEN_TELEGRAM, CANAL_GERAL, WHATSAPP_GROUP_ID

Como usar:
  python bridge_whatsapp.py

Dica: execute via iniciar_bridge.bat para iniciar automaticamente.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import asyncio
import logging
import os
import re
import time

from dotenv import load_dotenv
load_dotenv()

from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BRIDGE] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/bridge.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Proteção de instância única ───────────────────────────────────────────────
import atexit

_LOCK_FILE = os.path.join(os.path.dirname(__file__), "data", "bridge.lock")

def _verificar_instancia_unica():
    if os.path.exists(_LOCK_FILE):
        try:
            with open(_LOCK_FILE) as f:
                pid_antigo = int(f.read().strip())
            import psutil
            if psutil.pid_exists(pid_antigo):
                print(f"❌ Bridge já está rodando (PID {pid_antigo}). Feche a janela anterior primeiro.")
                sys.exit(1)
        except Exception:
            pass  # lock file inválido — sobrescreve
    with open(_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.remove(_LOCK_FILE) if os.path.exists(_LOCK_FILE) else None)

TOKEN = os.getenv("TOKEN_TELEGRAM", "")
CANAL_ID = os.getenv("CANAL_GERAL", "")
GROUP_ID = os.getenv("WHATSAPP_GROUP_ID", "")

# IDs de mensagem já processadas (evita duplicatas na mesma sessão)
_processados: set[int] = set()


def _extrair_produto_da_mensagem(texto: str, entidade_urls: list = None) -> dict | None:
    """Extrai dados básicos do produto a partir do texto do Telegram."""
    if not texto:
        return None

    # Extrai link
    link = ""
    if entidade_urls:
        for url in entidade_urls:
            if url:
                link = url
                break
    if not link:
        m = re.search(r"https?://\S+", texto)
        link = m.group(0) if m else ""

    # Extrai título (primeira linha não-vazia após emoji)
    linhas = [l.strip() for l in texto.split("\n") if l.strip()]
    titulo = ""
    for linha in linhas:
        # Remove emojis e marcadores do início
        limpa = re.sub(r"^[🔥💰🏷️✅➡️#\s*<>b/]+", "", linha).strip()
        if len(limpa) > 10:
            titulo = limpa[:80]
            break

    # Extrai preço
    preco = None
    m_preco = re.search(r"R\$\s*([\d.,]+)", texto)
    if m_preco:
        try:
            preco = float(m_preco.group(1).replace(".", "").replace(",", "."))
        except Exception:
            pass

    # Extrai desconto
    desconto_pct = 0
    m_desc = re.search(r"(\d+)%\s*OFF", texto, re.IGNORECASE)
    if m_desc:
        desconto_pct = int(m_desc.group(1))

    # Extrai cupom
    cupom = None
    m_cupom = re.search(r"CUPOM[:\s]+([A-Z0-9\-_]+)", texto, re.IGNORECASE)
    if m_cupom:
        cupom = m_cupom.group(1)

    if not titulo and not link:
        return None

    return {
        "titulo": titulo or "Oferta especial",
        "link": link,
        "preco": preco,
        "desconto_pct": desconto_pct,
        "cupom": cupom,
        "categoria": "geral",
    }


async def _enviar_whatsapp(produto: dict, texto_original: str) -> bool:
    """Gera conteúdo IA e envia para o grupo WhatsApp via automação do Chrome."""
    try:
        from core.ai_content import gerar_conteudo
        conteudo = gerar_conteudo(produto)
        mensagem_wa = conteudo.get("mensagem_whatsapp") or texto_original[:500]
        ia_usada = conteudo.get("ia_usada", False)
        log.info("Conteúdo %s para: %s", "IA ✨" if ia_usada else "padrão", produto.get("titulo", "")[:50])
    except Exception as e:
        log.warning("IA falhou: %s — usando texto original", e)
        mensagem_wa = texto_original[:500]

    if not mensagem_wa:
        return False

    if os.getenv("GITHUB_ACTIONS"):
        log.info("GitHub Actions detectado — WhatsApp local ignorado")
        return False

    return await asyncio.get_event_loop().run_in_executor(
        None, _enviar_via_pyautogui, mensagem_wa
    )


def _enviar_via_pyautogui(mensagem: str) -> bool:
    """
    Controla o WhatsApp Web aberto no Chrome para enviar mensagem no grupo.

    Sequência:
      1. Localiza a janela do Chrome com WhatsApp Web
      2. Foca a janela e abre a busca (Ctrl+Alt+/)
      3. Digita o nome do grupo, seleciona, abre o chat
      4. Cola a mensagem e envia
    """
    try:
        import pygetwindow as gw
        import pyautogui
        import pyperclip
    except ImportError:
        log.error("Instale: pip install pyautogui pygetwindow pyperclip")
        return False

    NOME_GRUPO = "Bot-Ofertas"
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.4

    # 1. Encontra a janela do Chrome com WhatsApp Web
    janelas = gw.getWindowsWithTitle("WhatsApp")
    if not janelas:
        log.error("WhatsApp Web não encontrado. Abra web.whatsapp.com no Chrome.")
        return False

    wa_janela = janelas[0]
    wa_janela.activate()
    time.sleep(1.2)

    try:
        # 2. Abre a caixa de busca de conversa (atalho do WhatsApp Web)
        pyautogui.hotkey("ctrl", "alt", "/")
        time.sleep(0.8)

        # 3. Digita o nome do grupo e aguarda autocomplete
        pyautogui.typewrite(NOME_GRUPO, interval=0.07)
        time.sleep(1.5)

        # 4. Seleciona o primeiro resultado e abre o chat
        pyautogui.press("down")
        time.sleep(0.3)
        pyautogui.press("enter")
        time.sleep(1.8)

        # 5. Cola a mensagem via clipboard (suporta emojis e quebras de linha)
        pyperclip.copy(mensagem)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)

        # 6. Envia
        pyautogui.press("enter")
        time.sleep(0.5)

        log.info("✅ WhatsApp: mensagem enviada no grupo %s", NOME_GRUPO)
        return True

    except Exception as e:
        log.error("pyautogui falhou: %s", e)
        return False


async def _handler_nova_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processa nova mensagem do canal Telegram."""
    msg = update.channel_post or update.message
    if not msg:
        return

    # Só processa mensagens do canal configurado
    chat_id = str(msg.chat_id)
    if CANAL_ID and chat_id != str(CANAL_ID):
        return

    msg_id = msg.message_id
    if msg_id in _processados:
        return
    _processados.add(msg_id)

    texto = msg.text or msg.caption or ""
    if not texto or len(texto) < 20:
        return

    # Filtra apenas mensagens de oferta (contêm link de produto)
    if not re.search(r"(meli\.la|mercadolivre\.com|amazon\.com\.br|amzn\.to)", texto, re.I):
        return

    log.info("Nova oferta detectada (msg #%d) — enviando para WhatsApp...", msg_id)

    # Extrai URLs das entidades
    urls = []
    if msg.entities:
        for ent in msg.entities:
            if ent.url:
                urls.append(ent.url)
    if msg.caption_entities:
        for ent in msg.caption_entities:
            if ent.url:
                urls.append(ent.url)

    produto = _extrair_produto_da_mensagem(texto, urls)
    if not produto:
        log.warning("Não consegui extrair produto da mensagem #%d", msg_id)
        return

    # Pequena pausa para não travar o WhatsApp Web
    await asyncio.sleep(3)
    await _enviar_whatsapp(produto, texto)


def main():
    _verificar_instancia_unica()

    if not TOKEN:
        print("❌ TOKEN_TELEGRAM não configurado no .env")
        return
    if not GROUP_ID:
        print("⚠️  WHATSAPP_GROUP_ID não configurado — WhatsApp desativado")

    print("=" * 55)
    print("🌉 BRIDGE TELEGRAM → WHATSAPP")
    print("=" * 55)
    print(f"📡 Monitorando canal: {CANAL_ID or 'todos'}")
    print(f"💚 Grupo WhatsApp: {GROUP_ID or 'não configurado'}")
    print(f"🤖 IA: {'ativa (Claude Sonnet)' if _ia_ok() else 'inativa (sem API key)'}")
    print("Ctrl+C para parar.\n")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(
        filters.ALL & (filters.ChatType.CHANNEL | filters.ChatType.GROUPS),
        _handler_nova_mensagem,
    ))
    app.run_polling(allowed_updates=["channel_post", "message"])


def _ia_ok() -> bool:
    try:
        from core.ai_content import ia_ativa
        return ia_ativa()
    except Exception:
        return False


if __name__ == "__main__":
    main()
