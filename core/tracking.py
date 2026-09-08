# -*- coding: utf-8 -*-
"""
TRACKING — marcação de origem por canal, sem nunca quebrar o afiliado.

Pedido do Daniel: "no link, troque o matt_source conforme o canal
(bot_telegram, bot_whatsapp, instagram, meta_ads). Assim você descobre de
onde vêm os cliques e quais canais realmente geram compra."

Regras 3, 4 e 11 do CLAUDE.md se aplicam inteiras aqui:
  - `matt_tool` (ML) e `tag` (Amazon) precisam sobreviver como parâmetro de
    QUERY de verdade — este módulo nunca os remove nem os reescreve;
  - a troca é feita com `urllib.parse`, nunca com `str.replace`. A versão
    anterior (`integrations/whatsapp_sender.py`) fazia
    `.replace("matt_source=bot_telegram", "matt_source=bot_whatsapp")`:
    funcionava só quando o valor era exatamente esse, e virava no-op
    silencioso em link encurtado (meli.la), link sem `matt_source`, ou com
    a origem já em outro valor;
  - o `#fragment` de tracking do carrossel do ML é descartado antes de
    qualquer coisa (bug de 2026-08-04) — deixá-lo passar esconde a query
    inteira do servidor do ML.
"""
from __future__ import annotations

from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

# canal lógico → valor gravado no parâmetro de origem
ORIGENS = {
    "telegram":  "bot_telegram",
    "whatsapp":  "bot_whatsapp",
    "instagram": "instagram",
    "facebook":  "facebook",
    "meta_ads":  "meta_ads",
    "tiktok":    "tiktok",
    "youtube":   "youtube",
    "twitter":   "twitter",
    "site":      "site",
    "n8n":       "n8n",
    "webhook":   "webhook",
}

_HOSTS_ML = ("mercadolivre.com.br", "mercadolibre.com", "meli.la", "mlb.com.br")
_HOSTS_AMAZON = ("amazon.com.br", "amazon.com", "amzn.to")


def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _e_ml(url: str) -> bool:
    h = _host(url)
    return any(h == d or h.endswith("." + d) for d in _HOSTS_ML)


def _e_amazon(url: str) -> bool:
    h = _host(url)
    return any(h == d or h.endswith("." + d) for d in _HOSTS_AMAZON)


def _com_parametros(url: str, novos: dict[str, str]) -> str:
    """Acrescenta/atualiza parâmetros de query preservando os existentes.

    `keep_blank_values=True` — um parâmetro vazio existente no link é dado
    do link, não lixo; descartá-lo mudaria a URL publicada sem necessidade.
    O fragmento é sempre removido (ver docstring do módulo).
    """
    p = urlsplit(url.strip())
    query = dict(parse_qsl(p.query, keep_blank_values=True))
    query.update({k: v for k, v in novos.items() if v})
    return urlunsplit((p.scheme or "https", p.netloc, p.path, urlencode(query), ""))


def marcar_origem(link: str, canal: str) -> str:
    """Devolve o link com a origem do canal marcada.

    Mercado Livre → `matt_source`; Amazon → `ascsubtag`; qualquer outro
    domínio (site próprio, página ponte) → `utm_source`/`utm_medium`.
    O link volta inalterado se for vazio ou não parecer uma URL.
    """
    if not link or not link.startswith("http"):
        return link or ""
    origem = ORIGENS.get(canal, canal or "desconhecido")
    if _e_ml(link):
        return _com_parametros(link, {"matt_source": origem})
    if _e_amazon(link):
        return _com_parametros(link, {"ascsubtag": origem})
    return _com_parametros(link, {"utm_source": origem, "utm_medium": "social"})


def origem_atual(link: str) -> str:
    """Lê a origem marcada no link (string vazia se não houver)."""
    if not link:
        return ""
    q = parse_qs(urlsplit(link).query)
    for chave in ("matt_source", "ascsubtag", "utm_source"):
        if q.get(chave):
            return q[chave][0]
    return ""


def afiliado_intacto(link: str, *, matt_tool: str = "", amazon_tag: str = "") -> bool:
    """Confirma, com parse_qs (nunca substring — Regra 3), que o parâmetro
    de afiliado chegou como query de verdade depois da marcação de origem.

    Usado nos testes e antes de publicar: é a checagem que impede uma
    mudança de tracking de comer a comissão silenciosamente.
    """
    if not link:
        return False
    q = parse_qs(urlsplit(link).query)
    if _e_ml(link) and matt_tool:
        return q.get("matt_tool", [None])[0] == matt_tool
    if _e_amazon(link) and amazon_tag:
        return q.get("tag", [None])[0] == amazon_tag
    # Encurtador oficial (meli.la/XXXX) já carrega a atribuição do lado do
    # ML — não há query pra conferir, e exigir uma reprovaria link válido.
    return True


def link_utm(url: str, *, origem: str, campanha: str = "grupo_ofertas",
             medio: str = "social", conteudo: str = "") -> str:
    """URL do site/página ponte com UTM completo, para medir divulgação."""
    return _com_parametros(url, {
        "utm_source": ORIGENS.get(origem, origem),
        "utm_medium": medio,
        "utm_campaign": campanha,
        "utm_content": conteudo,
    })
