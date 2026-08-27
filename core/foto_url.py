# -*- coding: utf-8 -*-
"""
FOTO URL — normalização de URL de imagem para ALTA RESOLUÇÃO e download
com cadeia de alternativas.

Problema relatado pelo Daniel (26/08/2026): "não está aparecendo as fotos".
Duas causas distintas, tratadas aqui:

1. **Resolução**. O card de listagem do Mercado Livre entrega a miniatura
   (`...-I.jpg`, e a variante 1x de `D_NQ_NP_`). Ela é pequena e, em alguns
   casos, some do CDN antes da versão original. A versão publicável é a
   `-O.jpg` na variante `D_NQ_NP_2X_`. `integrations/ml_scraper.py` já
   fazia essa troca por `str.replace("I.jpg", "O.jpg")` — mas só ele, e por
   substring solta: `"I.jpg"` casa no meio de qualquer nome de arquivo
   terminado em "I" (ex.: `..._MOBILE_UI.jpg`), corrompendo a URL. Aqui a
   troca é feita só no sufixo de variante do CDN, via regex ancorada.

2. **Recusa do CDN**. Quando o Telegram tenta buscar a URL sozinho, o
   `mlstatic` às vezes responde 403/404 ("Failed to get http url content"
   do lado do Telegram). A cadeia `variantes()` dá alternativas reais para
   tentar, e `baixar_melhor()` devolve os bytes da primeira que responder —
   o upload direto de bytes contorna o fetch do lado do Telegram.

Sem essa camada, a única saída era não publicar o produto (Regra 7), e o
mesmo produto reentrava na rodada seguinte e falhava de novo — o loop que
gerou 5 registros de "falha ao publicar" do MLB68674214.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger("foto_url")

# Sufixo de variante do CDN do ML, imediatamente antes da extensão:
#   .../D_NQ_NP_2X_811742-MLB123456789-O.jpg
#                                      ^^
_RE_VARIANTE_ML = re.compile(r"-([A-Z])(\.(?:jpg|jpeg|png|webp))$", re.IGNORECASE)

# Variante de tamanho da Amazon: ._AC_SX300_.jpg, ._SL160_.jpg, ...
_RE_VARIANTE_AMAZON = re.compile(r"\._[A-Z0-9_,]+_\.(jpg|jpeg|png)$", re.IGNORECASE)

_HOSTS_ML = ("mlstatic.com",)
_HOSTS_AMAZON = ("media-amazon.com", "images-amazon.com", "ssl-images-amazon.com")


def _sem_fragmento(url: str) -> str:
    """Remove o #fragment e força https.

    Mesmo cuidado da Regra 11 do CLAUDE.md: um `#fragment` de tracking
    sobrevivendo a um `split("?")` ingênuo quebra a URL. Aqui usamos
    urlsplit, que separa os dois corretamente.
    """
    p = urlsplit(url.strip())
    esquema = "https" if p.scheme in ("", "http", "https") else p.scheme
    return urlunsplit((esquema, p.netloc, p.path, p.query, ""))


def _e_host(url: str, hosts: tuple[str, ...]) -> bool:
    try:
        net = urlsplit(url).netloc.lower()
    except ValueError:
        return False
    return any(net.endswith(h) or f".{h}" in net for h in hosts)


def alta_resolucao(url: str) -> str:
    """Devolve a melhor URL conhecida da MESMA imagem.

    Mercado Livre: variante `-O` (original) e prefixo `D_NQ_NP_2X_`.
    Amazon: remove o modificador de tamanho (`._AC_SX300_.jpg` → `.jpg`),
    que o CDN interpreta como "resolução máxima disponível".
    Qualquer outra origem volta inalterada (só https + sem fragmento).
    """
    if not url or not isinstance(url, str):
        return ""
    u = _sem_fragmento(url)
    if _e_host(u, _HOSTS_ML):
        u = _RE_VARIANTE_ML.sub(lambda m: f"-O{m.group(2)}", u)
        if "D_NQ_NP_2X_" not in u:
            u = u.replace("D_NQ_NP_", "D_NQ_NP_2X_")
    elif _e_host(u, _HOSTS_AMAZON):
        u = _RE_VARIANTE_AMAZON.sub(lambda m: f".{m.group(1)}", u)
    return u


def variantes(url: str) -> list[str]:
    """URLs candidatas da mesma foto, da melhor para a mais tolerante.

    Ordem: alta resolução → original 1x → a URL como veio. Sem duplicatas e
    preservando a ordem (dict.fromkeys) — tentar a mesma URL duas vezes só
    dobra o tempo da falha.
    """
    if not url:
        return []
    candidatas = [alta_resolucao(url)]
    if _e_host(url, _HOSTS_ML):
        candidatas.append(alta_resolucao(url).replace("D_NQ_NP_2X_", "D_NQ_NP_"))
    candidatas.append(_sem_fragmento(url))
    return [c for c in dict.fromkeys(candidatas) if c]


def baixar_melhor(url: str, tentativas_por_variante: int = 2) -> tuple[bytes | None, str]:
    """Baixa a primeira variante que responder. Devolve (bytes, url_usada).

    (None, "") quando nenhuma variante respondeu — quem chama decide entre
    fallback sem foto e descartar o produto.
    """
    from core.net import baixar_bytes  # noqa: PLC0415  (evita ciclo no import)

    for candidata in variantes(url):
        dados = baixar_bytes(candidata, tentativas=tentativas_por_variante)
        if dados:
            log.info("Foto obtida (%d KB): %s", len(dados) // 1024, candidata[:110])
            return dados, candidata
    log.warning("Nenhuma variante da foto respondeu: %s", (url or "")[:110])
    return None, ""
