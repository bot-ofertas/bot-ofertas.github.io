# -*- coding: utf-8 -*-
"""
Guardas para o texto gerado por IA antes de ele virar post.

Por que existe (Regras 3, 4, 7 e 11 do CLAUDE.md):

`core/ai_content.py` mandava o link de afiliado DENTRO do prompt e pedia pro
modelo repetir esse link no fim da mensagem do WhatsApp. Esse texto vai
direto pro grupo como `mensagem_override`
(rastreador.py / rastreador_amazon.py -> db.enfileirar_whatsapp ->
integrations/whatsapp_sender.py), e ninguém conferia se o link que saiu era
o mesmo que entrou. Qualquer reescrita do modelo — encurtar, "embelezar",
cortar a query no meio quando bate o `max_tokens`, ou alucinar outra URL —
publica a oferta sem `matt_tool=` / `tag=`: comissão perdida sem nenhum
alarme disparar, exatamente o tipo de falha silenciosa do bug do
`#fragment` de 2026-08-04.

A saída é não deixar o modelo escrever URL nenhuma: o prompt pede o marcador
`{LINK}`, e este módulo (a) apaga qualquer URL que o modelo tenha inventado,
(b) põe o link real no lugar do marcador e (c) confirma com `parse_qs()` —
nunca com substring — que o parâmetro de afiliado chegou como query de
verdade, depois do "?" e antes de qualquer "#".
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

# Marcador que o prompt manda o modelo usar no lugar da URL.
PLACEHOLDER_LINK = "{LINK}"

# URLs "cruas" (http/https) e as escritas sem esquema (www.algo.com).
_RE_URL = re.compile(r"(?i)(?:https?://|www\.)[^\s<>\"'`]+")

# Parâmetro de afiliado obrigatório por loja. Mantido aqui só como mapa de
# consulta — a validação de valor fica com o provider de cada loja
# (affiliates/*), que é a fonte da verdade.
PARAM_AFILIADO = {
    "ml": "matt_tool",
    "mercadolivre": "matt_tool",
    "amazon": "tag",
}


def numero(valor) -> float | None:
    """Converte preço/desconto pra float sem estourar.

    Prompt é montado antes de qualquer try da chamada à API: preço que chegou
    como string ("1.299,90") ou None fazia o f-string com `:.2f` levantar
    ValueError/TypeError e derrubar a geração de conteúdo inteira.
    """
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    if not texto:
        return None
    if "," in texto:  # 1.299,90 -> 1299.90
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def remover_urls(texto: str) -> str:
    """Apaga toda URL do texto, preservando o resto da formatação.

    Linha que só existia pra carregar o link (ex.: "👉 https://...") sai
    inteira, senão sobra um "👉" órfão no post.
    """
    if not texto:
        return ""

    saida: list[str] = []
    for linha in texto.split("\n"):
        if not _RE_URL.search(linha):
            saida.append(linha)
            continue
        restante = _RE_URL.sub("", linha)
        # Sem nenhuma letra/número, a linha só servia de moldura pro link.
        if not re.search(r"\w", restante, flags=re.UNICODE):
            continue
        saida.append(re.sub(r"[ \t]{2,}", " ", restante).rstrip())

    limpo = "\n".join(saida)
    return re.sub(r"\n{3,}", "\n\n", limpo).strip()


def aplicar_link(texto: str, link: str) -> str:
    """Devolve o texto do modelo com o link REAL — e só ele — no fim.

    Toda URL escrita pelo modelo é descartada antes da substituição: o único
    link que sobra é o que nós geramos e validamos.
    """
    if not texto:
        return texto or ""
    if not link:
        return remover_urls(texto.replace(PLACEHOLDER_LINK, "")).strip()

    tem_marcador = PLACEHOLDER_LINK in texto
    limpo = remover_urls(texto)

    if tem_marcador and PLACEHOLDER_LINK in limpo:
        return limpo.replace(PLACEHOLDER_LINK, link)

    # Modelo ignorou o marcador (ou o marcador foi junto com a linha do link
    # alucinado): o link entra no fim, no mesmo formato do fallback.
    limpo = limpo.replace(PLACEHOLDER_LINK, "").rstrip()
    return f"{limpo}\n\n👉 {link}" if limpo else f"👉 {link}"


def link_preservado(texto: str, link: str) -> bool:
    """True se o texto tem o link exato e nenhuma outra URL além dele."""
    if not texto or not link:
        return False
    if link not in texto:
        return False
    outras = [u for u in _RE_URL.findall(texto) if u not in link]
    return not outras


def link_afiliado_valido(link: str, fonte: str = "") -> bool:
    """True se o link ainda carrega o parâmetro de afiliado como query real.

    Delega pro provider da loja (affiliates/*), que já faz a checagem com
    `urlsplit()` + `parse_qs()`. Sem provider conhecido, exige pelo menos que
    o parâmetro esperado da `fonte` exista como query de verdade.
    """
    if not link:
        return False
    try:
        from affiliates.registry import get_provider  # noqa: PLC0415

        provider = get_provider(link)
        if provider is not None:
            return bool(provider.validate_affiliate_link(link))
    except Exception:
        pass

    param = PARAM_AFILIADO.get((fonte or "").lower())
    if not param:
        return False
    valores = parse_qs(urlsplit(link).query).get(param) or []
    return bool(valores and valores[0].strip())
