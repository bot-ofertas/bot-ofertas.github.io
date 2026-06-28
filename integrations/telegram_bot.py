# -*- coding: utf-8 -*-
"""
Publicação de ofertas e chatbot de FAQ no Telegram.
"""
from __future__ import annotations

import html
import logging
import os
from datetime import datetime
from typing import Callable, Awaitable, Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes,
)
from telegram import Update

log = logging.getLogger(__name__)

# ── Configuração de admin ──────────────────────────────────────────────────────
# IDs de chat autorizados a usar /status e /stats (separados por vírgula na env var ADMIN_IDS)
_ADMIN_IDS: set[int] = set(
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
)

# ── FAQ ───────────────────────────────────────────────────────────────────────
_FAQ: dict[str, tuple[str, list[str]]] = {
    "frete": (
        "A maioria dos produtos tem frete grátis para assinantes Prime ou em compras acima do valor mínimo. "
        "Confira o ícone de caminhão na página do produto para confirmar.",
        ["frete", "entrega grátis", "envio", "frete gratis", "sem frete"],
    ),
    "garantia": (
        "Todos os produtos têm garantia do vendedor e proteção Mercado Livre. "
        "Em caso de problemas, acesse 'Minhas Compras' e abra uma reclamação.",
        ["garantia", "defeito", "quebrado", "com defeito", "estrago"],
    ),
    "prazo": (
        "O prazo de entrega aparece antes de finalizar a compra, na página do produto. "
        "Produtos Full chegam em 1-2 dias úteis.",
        ["prazo", "tempo", "quando chega", "data", "demora", "chegar"],
    ),
    "autenticidade": (
        "Publicamos apenas ofertas de vendedores com reputação verde no Mercado Livre. "
        "Verifique sempre o termômetro de reputação na página do vendedor.",
        ["autenticidade", "falso", "original", "genuino", "legítimo", "verdadeiro"],
    ),
    "desconto": (
        "Os descontos são calculados com base no preço original registrado no momento da publicação. "
        "Descontos acima de 20% já são considerados ótimas ofertas.",
        ["desconto", "promoção", "oferta", "barato", "preço", "preco", "economia"],
    ),
    "devolucao": (
        "O Mercado Livre oferece política de devolução em até 7 dias após o recebimento. "
        "Acesse 'Minhas Compras' → produto → 'Devolver'.",
        ["devolucao", "devolução", "devolver", "reembolso", "reembolsar", "troca", "retorno"],
    ),
    "pagamento": (
        "Aceitamos cartão de crédito (em até 12x), débito, Pix e boleto. "
        "Pagamentos via Mercado Pago têm proteção extra.",
        ["pagamento", "pagar", "parcelamento", "parcelas", "pix", "boleto", "cartão", "cartao"],
    ),
    "rastreio": (
        "Após a confirmação do pagamento, você recebe um código de rastreio por e-mail e no app do Mercado Livre. "
        "Use o código nos Correios ou na transportadora indicada.",
        ["rastreio", "rastrear", "rastreamento", "código", "codigo", "cep", "correios"],
    ),
    "cancelamento": (
        "Você pode cancelar a compra antes do envio diretamente em 'Minhas Compras'. "
        "Após o envio, é necessário recusar o pacote ou aguardar e pedir devolução.",
        ["cancelamento", "cancelar", "cancela", "desistir"],
    ),
    "parcelas": (
        "A maioria das ofertas permite parcelamento em até 12x sem juros no cartão. "
        "O valor mínimo de parcela pode variar conforme o banco emissor.",
        ["parcelas", "parcelado", "12x", "sem juros", "juros", "parcela"],
    ),
    "fatura": (
        "A nota fiscal é emitida pelo vendedor e enviada junto ao produto ou por e-mail. "
        "Para solicitar, entre em contato com o vendedor pela central de mensagens do ML.",
        ["fatura", "nota fiscal", "nota", "nf", "danfe", "recibo"],
    ),
    "cupom": (
        "Cupons de desconto do Mercado Livre podem ser aplicados na finalização da compra. "
        "Fique atento às notificações do app para cupons exclusivos.",
        ["cupom", "coupon", "código de desconto", "codigo de desconto", "voucher"],
    ),
}


def _é_admin(user_id: int) -> bool:
    return not _ADMIN_IDS or user_id in _ADMIN_IDS


# ── Montagem da mensagem ──────────────────────────────────────────────────────

def _montar_mensagem(
    produto: dict,
    titulo_reescrito: str | None = None,
    descricao_reescrita: str | None = None,
) -> str:
    titulo = html.escape(titulo_reescrito or produto.get("titulo") or "Sem título")
    preco: float | None = produto.get("preco")
    preco_original: float | None = produto.get("preco_original")
    link: str = produto.get("link") or produto.get("affiliate_link") or "#"
    score: int = produto.get("score", 0)
    categoria: str = produto.get("categoria") or produto.get("canal") or "geral"
    cupom: str | None = produto.get("cupom")

    linhas = [f"🛍️ <b>{titulo}</b>", ""]

    if preco_original and preco and preco_original > preco:
        desconto = int(round((1 - preco / preco_original) * 100))
        linhas.append(
            f"De <s>R$ {preco_original:.2f}</s> por <b>R$ {preco:.2f}</b> "
            f"({desconto}% OFF) 🔥"
        )
    elif preco:
        linhas.append(f"💰 <b>R$ {preco:.2f}</b>")

    if cupom:
        linhas.append(f"🎟️ <b>Cupom disponível:</b> <code>{html.escape(cupom)}</code>")

    if descricao_reescrita:
        linhas.append("")
        linhas.append(html.escape(descricao_reescrita))

    if score >= 75:
        linhas.append("")
        linhas.append("⭐ <i>Oferta em destaque</i>")

    linhas += [
        "",
        f'🔗 <a href="{html.escape(link, quote=True)}">Ver oferta no Mercado Livre</a>',
        "",
        f"#{html.escape(categoria.lower().replace(' ', '_'))} #oferta #publicidade",
    ]
    return "\n".join(linhas)


def _montar_teclado(produto: dict) -> InlineKeyboardMarkup:
    link: str = produto.get("link") or produto.get("affiliate_link") or "#"
    titulo = produto.get("titulo") or "Oferta"
    texto_compartilhar = f"https://t.me/share/url?url={html.escape(link, quote=True)}&text={html.escape(titulo, quote=True)}"
    botoes = [
        [
            InlineKeyboardButton("Ver Oferta 🛒", url=link),
            InlineKeyboardButton("Compartilhar 📤", url=texto_compartilhar),
        ]
    ]
    return InlineKeyboardMarkup(botoes)


# ── Publicação ────────────────────────────────────────────────────────────────

async def publicar(
    bot: Bot,
    produto: dict,
    canais: dict,
    titulo_reescrito: str | None = None,
) -> bool:
    """Publica uma oferta no canal Telegram correspondente.

    A assinatura desta função é estável — não altere sem atualizar todos os callers.
    """
    canal_nome = produto.get("canal") or "geral"
    chat_id = canais.get(canal_nome) or next(iter(canais.values()))
    mensagem = _montar_mensagem(produto, titulo_reescrito)
    teclado = _montar_teclado(produto)

    try:
        if produto.get("foto"):
            await bot.send_photo(
                chat_id=chat_id,
                photo=produto["foto"],
                caption=mensagem,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=mensagem,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado,
                disable_web_page_preview=False,
            )
        return True
    except Exception as e:
        log.error("Erro ao publicar '%s': %s", produto.get("titulo"), e)
        return False


async def publicar_com_ia(
    bot: Bot,
    produto: dict,
    canais: dict,
    rewriter: Callable[[dict], Awaitable[dict[str, str] | None]],
) -> bool:
    """Tenta reescrever título/descrição com IA antes de publicar.

    O parâmetro `rewriter` é um callable assíncrono que recebe o produto e
    deve retornar um dict com chaves opcionais 'titulo' e 'descricao', ou None
    se a reescrita falhar.  Em qualquer caso de falha, faz fallback para
    publicar() sem reescrita.

    Args:
        bot: instância do Bot Telegram.
        produto: dicionário com dados do produto.
        canais: mapa canal_nome -> chat_id.
        rewriter: async callable(produto) -> {"titulo": ..., "descricao": ...} | None.

    Returns:
        True se a publicação foi bem-sucedida, False caso contrário.
    """
    titulo_reescrito: str | None = None
    descricao_reescrita: str | None = None

    try:
        resultado = await rewriter(produto)
        if isinstance(resultado, dict):
            titulo_reescrito = resultado.get("titulo") or None
            descricao_reescrita = resultado.get("descricao") or None
    except Exception as e:
        log.warning("Reescrita IA falhou para '%s': %s — publicando sem IA.", produto.get("titulo"), e)

    canal_nome = produto.get("canal") or "geral"
    chat_id = canais.get(canal_nome) or next(iter(canais.values()))
    mensagem = _montar_mensagem(produto, titulo_reescrito, descricao_reescrita)
    teclado = _montar_teclado(produto)

    try:
        if produto.get("foto"):
            await bot.send_photo(
                chat_id=chat_id,
                photo=produto["foto"],
                caption=mensagem,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=mensagem,
                parse_mode=ParseMode.HTML,
                reply_markup=teclado,
                disable_web_page_preview=False,
            )
        return True
    except Exception as e:
        log.error("Erro ao publicar (com IA) '%s': %s", produto.get("titulo"), e)
        return False


# ── Handlers do chatbot ───────────────────────────────────────────────────────

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Olá! Sou o assistente de ofertas do canal.\n\n"
        "Posso responder dúvidas sobre:\n"
        "• Frete e prazo de entrega\n"
        "• Garantia e autenticidade\n"
        "• Devolução e cancelamento\n"
        "• Formas de pagamento e parcelamento\n"
        "• Rastreio de pedidos\n\n"
        "É só me perguntar! 😊"
    )


async def _cmd_ofertas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Acompanhe o canal para as melhores ofertas do dia! 🛍️\n"
        "As ofertas são publicadas automaticamente assim que detectamos ótimos descontos."
    )


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id if update.effective_user else 0
    if not _é_admin(user_id):
        await update.message.reply_text("⛔ Acesso restrito a administradores.")
        return

    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    await update.message.reply_text(
        f"✅ <b>Bot operacional</b>\n"
        f"🕐 Hora atual: {agora}\n"
        f"🤖 Versão: bot_ofertas v2\n"
        f"📡 Polling ativo",
        parse_mode=ParseMode.HTML,
    )


async def _cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id if update.effective_user else 0
    if not _é_admin(user_id):
        await update.message.reply_text("⛔ Acesso restrito a administradores.")
        return

    try:
        from core import database as db  # importação local para evitar circular import
        s = db.stats()
        taxa = s.get("taxa_afiliado", 0)
        texto = (
            f"📊 <b>Estatísticas do Bot</b>\n\n"
            f"📦 Total de produtos: <b>{s['total']}</b>\n"
            f"📤 Enviados: <b>{s['enviados']}</b>\n"
            f"⏳ Pendentes: <b>{s['pendentes']}</b>\n"
            f"♻️ Duplicatas: <b>{s['duplicatas']}</b>\n\n"
            f"🔗 Links de afiliado OK: <b>{s['afiliado_ok']}</b>\n"
            f"❌ Links com falha: <b>{s['afiliado_falha']}</b>\n"
            f"📈 Taxa de sucesso afiliado: <b>{taxa}%</b>\n\n"
            f"⭐ Score médio: <b>{s['score_medio']}</b>"
        )
        await update.message.reply_text(texto, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.error("Erro ao buscar stats: %s", e)
        await update.message.reply_text(f"❌ Erro ao buscar estatísticas: {e}")


async def _responder_faq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texto = (update.message.text or "").lower()

    # Busca por palavras-chave em cada entrada do FAQ (sem necessidade de match exato)
    melhor: tuple[str, int] | None = None  # (resposta, contagem de palavras batidas)
    for _chave, (resposta, palavras_chave) in _FAQ.items():
        contagem = sum(1 for kw in palavras_chave if kw in texto)
        if contagem > 0:
            if melhor is None or contagem > melhor[1]:
                melhor = (resposta, contagem)

    if melhor:
        await update.message.reply_text(melhor[0])
        return

    tópicos = ", ".join(
        kws[0] for _, kws in _FAQ.values()
    )
    await update.message.reply_text(
        "Não identifiquei sua dúvida. Posso ajudar com temas como:\n"
        "frete, garantia, prazo, devolução, pagamento, rastreio, cancelamento, cupom e mais.\n\n"
        "Tente reformular sua pergunta! 😊"
    )


# ── Criação da aplicação ──────────────────────────────────────────────────────

def criar_aplicacao(token: str):
    """Cria o Application do Telegram para rodar o chatbot em modo polling."""
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("ofertas", _cmd_ofertas))
    app.add_handler(CommandHandler("status", _cmd_status))
    app.add_handler(CommandHandler("stats", _cmd_stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _responder_faq))
    return app
