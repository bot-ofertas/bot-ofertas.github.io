# -*- coding: utf-8 -*-
"""
Testes do conteúdo gerado por IA — foco em não publicar post sem o link de
afiliado íntegro (Regras 3, 4, 7 e 11 do CLAUDE.md).

Nenhum teste chama a API: o cliente Anthropic é substituído por um dublê.

Rodar:
    python -m pytest tests/ -v
"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.ai_content as ai_content
import core.ai_rewriter as ai_rewriter
from core.ai_safety import (
    PLACEHOLDER_LINK,
    aplicar_link,
    link_afiliado_valido,
    link_preservado,
    numero,
    remover_urls,
)

LINK_ML = (
    "https://www.mercadolivre.com.br/fone-x/p/MLB123"
    "?matt_tool=47114387&matt_source=bot_telegram"
)


# ── ai_safety: limpeza de URL ─────────────────────────────────────────────────

def test_remover_urls_tira_link_e_a_linha_que_so_carregava_ele():
    texto = "🔥 Oferta top\n\n💰 R$ 99\n\n👉 https://encurtado.fake/abc\n\nCorre!"
    limpo = remover_urls(texto)
    assert "encurtado.fake" not in limpo
    assert "👉" not in limpo          # linha órfã não sobra no post
    assert "Corre!" in limpo


def test_remover_urls_pega_dominio_sem_esquema():
    assert "www.amazon.com.br" not in remover_urls("Compre em www.amazon.com.br hoje")


# ── ai_safety: injeção e validação do link real ───────────────────────────────

def test_aplicar_link_troca_o_marcador_pelo_link_real():
    texto = f"Oferta boa\n\n{PLACEHOLDER_LINK}"
    assert aplicar_link(texto, LINK_ML) == f"Oferta boa\n\n{LINK_ML}"


def test_aplicar_link_descarta_url_alucinada_pelo_modelo():
    texto = f"Oferta boa\n\n👉 https://ml.com/oferta-falsa\n{PLACEHOLDER_LINK}"
    final = aplicar_link(texto, LINK_ML)
    assert "oferta-falsa" not in final
    assert final.count(LINK_ML) == 1


def test_aplicar_link_acrescenta_o_link_se_o_modelo_esquecer_o_marcador():
    final = aplicar_link("Oferta boa sem marcador", LINK_ML)
    assert final.endswith(LINK_ML)


def test_link_preservado_reprova_link_mutilado():
    # max_tokens cortando a query no meio: some o matt_tool do link publicado.
    cortado = "https://www.mercadolivre.com.br/fone-x/p/MLB123?matt_"
    assert link_preservado(cortado, LINK_ML) is False
    assert link_preservado(f"veja {LINK_ML} agora", LINK_ML) is True


def test_link_afiliado_valido_com_query_e_fragmento_juntos():
    # Regra 11: URL do ML com "?" e "#" ao mesmo tempo.
    assert link_afiliado_valido(f"{LINK_ML}#polycard_client=search", "ml") is True
    # matt_tool preso DENTRO do fragmento nunca chega no servidor do ML.
    preso = "https://www.mercadolivre.com.br/p/MLB123#polycard&matt_tool=47114387"
    assert link_afiliado_valido(preso, "ml") is False


def test_link_afiliado_valido_amazon_exige_tag_como_query():
    ok = "https://www.amazon.com.br/dp/B0X?tag=silver1230c-20&linkCode=as2"
    assert link_afiliado_valido(ok, "amazon") is True
    assert link_afiliado_valido("https://www.amazon.com.br/dp/B0X", "amazon") is False


def test_numero_aceita_preco_em_formato_brasileiro():
    assert numero("R$ 1.299,90") == 1299.90
    assert numero(None) is None
    assert numero("preço sob consulta") is None


# ── ai_content: parsing da resposta ───────────────────────────────────────────

def test_extrair_json_com_prefill_sem_chave_de_abertura():
    dados = ai_content._extrair_json('"titulo_telegram": "Oi"}')
    assert dados["titulo_telegram"] == "Oi"


def test_extrair_json_dentro_de_cerca_de_codigo():
    dados = ai_content._extrair_json('```json\n{"titulo_telegram": "Oi"}\n```')
    assert dados["titulo_telegram"] == "Oi"


# ── ai_content: fluxo completo com cliente dublê ──────────────────────────────

def _cliente_falso(payload: dict):
    """Dublê do cliente Anthropic devolvendo o JSON pedido."""
    bloco = types.SimpleNamespace(type="text", text=json.dumps(payload)[1:])

    class _Messages:
        def create(self, **_kwargs):
            return types.SimpleNamespace(content=[bloco])

    return types.SimpleNamespace(messages=_Messages())


def _produto():
    return {
        "id": "MLB123", "titulo": "Fone Bluetooth X", "preco": 99.9,
        "preco_original": 199.9, "categoria": "audio", "fonte": "ml",
        "link": LINK_ML,
    }


def _com_cliente(monkeypatch, payload):
    ai_content._cache.clear()
    monkeypatch.setattr(ai_content, "_get_client", lambda: _cliente_falso(payload))
    monkeypatch.setattr(ai_content, "_bloqueado_ate", 0.0)
    return ai_content.gerar_conteudo(_produto())


def test_gerar_conteudo_substitui_link_inventado_pelo_real(monkeypatch):
    r = _com_cliente(monkeypatch, {
        "titulo_telegram": "🔥 Fone Bluetooth X com 50% OFF",
        "descricao_telegram": "Som top. Veja em https://link-falso.com/x",
        "mensagem_whatsapp": "🔥 Fone X\n💰 R$ 99,90\n\n👉 https://bit.ly/falso\n"
                             + PLACEHOLDER_LINK,
    })
    assert r["ia_usada"] is True
    assert "link-falso.com" not in r["descricao_telegram"]
    assert "bit.ly" not in r["mensagem_whatsapp"]
    assert link_preservado(r["mensagem_whatsapp"], LINK_ML)


def test_gerar_conteudo_cai_no_fallback_se_o_link_nao_sobreviver(monkeypatch):
    # Modelo devolve texto vazio no campo do WhatsApp: sem link nenhum.
    r = _com_cliente(monkeypatch, {
        "titulo_telegram": "🔥 Fone Bluetooth X",
        "descricao_telegram": "Som top",
        "mensagem_whatsapp": "",
    })
    assert LINK_ML in r["mensagem_whatsapp"]      # veio do fallback determinístico
    assert "OFERTA IMPERDÍVEL" in r["mensagem_whatsapp"]


def test_gerar_conteudo_respeita_60_caracteres_no_titulo(monkeypatch):
    r = _com_cliente(monkeypatch, {
        "titulo_telegram": "🔥 " + "T" * 200,
        "descricao_telegram": "x",
        "mensagem_whatsapp": PLACEHOLDER_LINK,
    })
    assert len(r["titulo_telegram"]) <= 60


def test_fallback_nao_quebra_com_preco_em_string():
    p = {"titulo": "Fone", "preco": "1.299,90", "preco_original": "1999,00",
         "link": LINK_ML}
    r = ai_content._fallback(p)
    assert "1299.90" in r["mensagem_whatsapp"]
    assert LINK_ML in r["mensagem_whatsapp"]


def test_chave_cache_muda_quando_o_preco_muda():
    p1 = _produto()
    p2 = dict(p1, preco=79.9)
    assert ai_content._chave_cache(p1) != ai_content._chave_cache(p2)


# ── ai_rewriter ───────────────────────────────────────────────────────────────

def test_cortar_nao_parte_palavra_ao_meio():
    titulo = "Fone Bluetooth JBL Tune 520BT com bateria de 57 horas"
    cortado = ai_rewriter._cortar(titulo, 40)
    assert len(cortado) <= 40
    assert not cortado.endswith(" ")
    assert titulo.startswith(cortado)
