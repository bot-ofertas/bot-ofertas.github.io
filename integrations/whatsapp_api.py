# -*- coding: utf-8 -*-
"""
WHATSAPP API — Evolution API (não-oficial, suporta GRUPOS)
===========================================================
A WhatsApp Cloud API oficial da Meta NÃO permite enviar para grupos —
apenas 1:1. Para postar em grupos automaticamente, usamos a Evolution API
(open-source, roda em Docker).

Setup:
    docker compose -f docker/evolution.yml up -d
    # Acessa http://localhost:8080, cria instância "botofertas"
    # Escaneia QR pelo celular (uma vez), pega apikey no .env

Variáveis .env:
    WHATSAPP_WEBHOOK_URL   = http://localhost:8080
    WHATSAPP_API_KEY       = sua_apikey_da_instancia
    WHATSAPP_INSTANCE      = botofertas
    WHATSAPP_GROUP_ID      = 120363XXXXXXXXX@g.us  (JID do grupo)

Endpoints Evolution v2 usados:
    POST /message/sendText/{instance}       → texto puro
    POST /message/sendMedia/{instance}      → foto + legenda
    GET  /instance/connectionState/{instance} → verifica se está logado
    GET  /group/fetchAllGroups/{instance}   → lista grupos + JIDs

Suporte a n8n:
  - Erros vão para data/errors.jsonl
  - Estado da conexão em GET /health
"""
from __future__ import annotations

import base64
import io
import logging
import os
from typing import Optional

log = logging.getLogger("whatsapp_api")

# Janela em que uma falha IDENTICA (mesma operacao, mensagem e contexto) nao
# e registrada de novo — ver a docstring de `_registrar`. 10 min fica bem
# abaixo do intervalo de envio (30-45 min) e bem acima da sondagem do
# `/health`.
_JANELA_ANTI_REPETICAO_S = 600
_ULTIMO_REGISTRO: dict = {}


def _registrar(operacao: str, mensagem: str, contexto: dict | None = None) -> None:
    """Manda a falha para o mesmo lugar que o Daniel ja olha.

    A Regra 11 pede que toda integracao externa logue falha via
    `core/error_logger.py` — nunca falhar silenciosamente. Aqui so as
    excecoes Python chegavam la: um HTTP 401/404/500 da Evolution devolvia
    `False` depois de um `log.warning`, que mora em `data/bot.log` e nao
    entra nem no `errors.jsonl` nem no bloco de notas da Area de Trabalho
    (Regra 9). Na pratica a oferta nao ia para o grupo e o unico rastro
    ficava numa linha de log que ninguem le.

    `registrar_evento` existe exatamente para isso: falha reportada como
    condicao de negocio (retorno False), sem forjar uma excecao falsa so
    para reusar `log_erro`. O import e tardio e o try/except e total —
    problema no logger nunca pode derrubar o envio.

    COM REPETICAO ABAFADA, e nao por elegancia. `esta_conectada()` e um
    caminho de LEITURA: quem chama e o `GET /health`, o `status.ps1`, o
    `diagnostico_whatsapp.py` e o workflow 01 do n8n a cada 15 min. Registrar
    "instancia desconectada" a cada consulta encheria o
    "Problemas de execucao para corrigir.txt" da Area de Trabalho com o mesmo
    bloco dezenas de vezes por dia (Regra 9: esse arquivo e para o Daniel
    ler) — o relatorio viraria ruido e esconderia o erro seguinte. A chave
    inclui o CONTEXTO, entao duas ofertas diferentes que falham no mesmo
    minuto continuam valendo dois registros; o que some e a mesma falha,
    identica, repetida pela sondagem. A janela e menor que o intervalo de
    envio (30-45 min, Regra 5), entao nenhuma falha de envio e engolida.
    """
    import time  # noqa: PLC0415

    chave = (operacao, mensagem, tuple(sorted((contexto or {}).items(), key=str)))
    agora = time.time()
    anterior = _ULTIMO_REGISTRO.get(chave)
    if anterior is not None and (agora - anterior) < _JANELA_ANTI_REPETICAO_S:
        log.debug("[%s] repetido em menos de %ds — nao registrado de novo",
                  operacao, _JANELA_ANTI_REPETICAO_S)
        return
    if len(_ULTIMO_REGISTRO) > 256:  # teto de memoria do processo longo
        _ULTIMO_REGISTRO.clear()
    _ULTIMO_REGISTRO[chave] = agora

    try:
        from core.error_logger import registrar_evento  # noqa: PLC0415
        registrar_evento(operacao, mensagem, contexto or {})
    except Exception:  # pragma: no cover — logger nunca derruba o envio
        log.warning("[%s] %s", operacao, mensagem)


def _placeholder(valor: str) -> bool:
    """True para os valores de exemplo do `.env.example`.

    Um placeholder e uma string nao-vazia, entao passaria por configuracao
    de verdade e ligaria o envio apontando para um grupo que nao existe —
    pior que estar desligado, porque a fila consome o item e o marca como
    processado. Mesma convencao do `setup_n8n._efetivo`.

    A checagem por prefixo sozinha nao cobria o exemplo que o
    `deploy/.env.example` traz para a nuvem: `120363XXXXXXXXX@g.us`. Ele nao
    comeca com nenhum dos prefixos, entao o envio respondia "configurado"
    num servidor recem-instalado e a fila drenava as ofertas para um JID que
    nao existe — em silencio, uma a cada 30-45 min. Um JID de verdade e so
    digitos antes do `@`, entao a corrida de `X` maiusculos e assinatura
    segura de exemplo nao preenchido.

    Mora AQUI, e nao no `whatsapp_sender`, porque este modulo e o mais
    baixo dos dois (o sender importa a API, nunca o contrario) e porque
    `_configurada()` e consultado por quem o `wa_ativo()` do sender nem
    passa perto: `core/healthcheck.py`, `diagnostico_whatsapp.py` e o
    `fila_tera_quem_envie()`. Enquanto a checagem existia so no sender, o
    `wa_ativo()` barrava o JID de exemplo e o `_configurada()` deixava
    passar — e como a Evolution API e a TENTATIVA 1 de
    `enviar_para_grupo()`, quem instalasse do zero mandava para o JID falso
    pelo caminho da API, exatamente o bug que a checagem foi escrita para
    impedir (verificado em 2026-09-18). Uma regra so, um lugar so — mesmo
    principio da Regra 15 para os horarios.
    """
    v = (valor or "").lower()
    if v.startswith(("cole_aqui", "cole-aqui", "seu_", "sua_", "exemplo")):
        return True
    return "xxx" in v


def _config() -> dict:
    grupo = (os.getenv("WHATSAPP_GROUP_ID", "") or "").strip()
    return {
        "url": (os.getenv("WHATSAPP_WEBHOOK_URL", "") or "").rstrip("/"),
        "key": os.getenv("WHATSAPP_API_KEY", ""),
        "instance": os.getenv("WHATSAPP_INSTANCE", "botofertas"),
        "group_id": "" if _placeholder(grupo) else grupo,
    }


def _configurada() -> bool:
    c = _config()
    return bool(c["url"] and c["key"] and c["group_id"])


def esta_conectada() -> bool:
    """Retorna True se a instância Evolution está logada no WhatsApp.

    Todo caminho de `False` diz POR QUE. O `except Exception: return False`
    anterior tratava "o container nao esta de pe", "a apikey esta errada" e
    "o QR caiu do celular" como a mesma coisa: um False mudo. Quem consome
    isso e o `/health` e o `status.ps1`, que mostravam
    `evolution-desconectada` sem nenhuma pista do que consertar, e o
    `enviar_oferta_completa()`, que desiste do envio com base nessa
    resposta. Causa diferente, conserto diferente — entao a causa e
    registrada (Regra 11).
    """
    c = _config()
    if not (c["url"] and c["key"]):
        return False
    try:
        import requests  # noqa: PLC0415
        r = requests.get(
            f"{c['url']}/instance/connectionState/{c['instance']}",
            headers={"apikey": c["key"]},
            timeout=6,
        )
        if r.status_code != 200:
            _registrar(
                "wa_api.estado_http",
                f"connectionState devolveu HTTP {r.status_code}: {r.text[:200]}",
                {"instance": c["instance"], "status": r.status_code},
            )
            return False
        estado = r.json().get("instance", {}).get("state", "")
        if estado != "open":
            # Estado != open e a situacao normal antes do QR ser lido: nao e
            # erro de codigo, mas e o motivo de nenhuma oferta sair, entao
            # precisa aparecer em algum lugar alem do /health.
            _registrar(
                "wa_api.desconectada",
                f"instancia '{c['instance']}' com estado '{estado or 'desconhecido'}' "
                f"(esperado 'open') — leia o QR: python setup_whatsapp_api.py qr",
                {"instance": c["instance"], "estado": estado},
            )
            return False
        return True
    except Exception as e:
        from core.error_logger import log_erro  # noqa: PLC0415
        log_erro("wa_api.estado_falhou", e, {"instance": c["instance"], "url": c["url"]})
        return False


def _baixar_foto_base64(url: str) -> Optional[str]:
    """Baixa foto do produto e retorna base64 (formato exigido pela Evolution API)."""
    if not url or not url.startswith("http"):
        return None
    try:
        import requests  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
        r = requests.get(url, timeout=10)
        if r.status_code != 200 or not r.content:
            # O mlstatic devolvendo 403 e caso conhecido (Regra 7) e aqui
            # custa a oferta inteira, porque a Regra 5 nao deixa publicar
            # sem foto no WhatsApp. Sem este registro, o unico rastro era o
            # `log.warning` do chamador.
            _registrar(
                "wa_api.foto_indisponivel",
                f"CDN devolveu HTTP {r.status_code} ({len(r.content or b'')} bytes)",
                {"url": url[:120], "status": r.status_code},
            )
            return None
        # Otimiza para envio mais rápido
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        img.thumbnail((900, 900))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        from core.error_logger import log_erro  # noqa: PLC0415
        log_erro("wa_api.foto_falhou", e, {"url": url[:120]})
        return None


# So estes codigos provam que o corpo v2 foi RECUSADO pelo formato — e so
# neles a segunda tentativa, com o envelope v1, e segura. Repetir o POST em
# 5xx ou timeout e aposta de post duplicado no grupo: a Evolution ja entregou
# a mensagem ao WhatsApp e falhou depois, e o grupo recebe a mesma oferta duas
# vezes — o estrago da Regra 11, so que por outro caminho. Em duvida, a
# quarentena da Regra 12 tenta de novo na proxima rodada; duplicar nao tem
# desfazer.
_STATUS_FORMATO_RECUSADO = (400, 422)


def enviar_texto(mensagem: str) -> bool:
    """Envia texto puro ao grupo via Evolution API.

    Nao e o caminho de oferta: a Regra 5 exige foto + legenda numa unidade
    so, e quem publica oferta e `enviar_oferta_completa()`. Este aqui serve
    ao teste de conexao do `setup_whatsapp_api.py` e a avisos operacionais.
    """
    c = _config()
    if not _configurada():
        return False
    url = f"{c['url']}/message/sendText/{c['instance']}"
    headers = {"apikey": c["key"], "Content-Type": "application/json"}
    try:
        import requests  # noqa: PLC0415
        # Evolution v2 espera body direto (sem envelope 'textMessage')
        r = requests.post(
            url, headers=headers,
            json={"number": c["group_id"], "text": mensagem},
            timeout=15,
        )
        if r.status_code in (200, 201):
            log.info("✅ WA API: texto enviado para %s", c["group_id"])
            return True

        if r.status_code not in _STATUS_FORMATO_RECUSADO:
            _registrar(
                "wa_api.envio_texto",
                f"sendText devolveu HTTP {r.status_code}: {r.text[:200]}",
                {"grupo": c["group_id"], "status": r.status_code},
            )
            return False

        # Compatibilidade v1 — so depois de o servidor dizer que o FORMATO
        # esta errado, nunca depois de um 5xx (ver _STATUS_FORMATO_RECUSADO).
        r2 = requests.post(
            url, headers=headers,
            json={"number": c["group_id"], "textMessage": {"text": mensagem}},
            timeout=15,
        )
        if r2.status_code in (200, 201):
            log.info("✅ WA API (v1): texto enviado para %s", c["group_id"])
            return True
        # Registrava `r` (a resposta v2) depois de tentar a v1: o relatorio
        # mostrava o erro da tentativa que nao foi a ultima, e o motivo real
        # da recusa do envelope v1 sumia. Agora os dois aparecem.
        _registrar(
            "wa_api.envio_texto",
            f"sendText recusado nas duas versoes — v2 HTTP {r.status_code}: "
            f"{r.text[:150]} | v1 HTTP {r2.status_code}: {r2.text[:150]}",
            {"grupo": c["group_id"], "status_v2": r.status_code,
             "status_v1": r2.status_code},
        )
        return False
    except Exception as e:
        from core.error_logger import log_erro  # noqa: PLC0415
        log_erro("wa_api.envio_texto", e, {"grupo": c["group_id"]})
        return False


def enviar_foto_legenda(foto_url: str, legenda: str) -> bool:
    """Envia foto + legenda ao grupo via Evolution API."""
    c = _config()
    if not _configurada():
        return False
    foto_b64 = _baixar_foto_base64(foto_url) if foto_url else None
    if not foto_b64:
        # Abortar e o comportamento correto: a Regra 5 exige foto + legenda
        # numa unidade so e a excecao da Regra 7 (publicar so com o preview
        # do link) foi decidida para o Telegram, nao para o WhatsApp. O que
        # faltava era o registro — assim a oferta nao publicada aparece no
        # relatorio em vez de sumir.
        _registrar(
            "wa_api.envio_sem_foto",
            "sem foto disponivel — envio abortado (Regra 5: foto + legenda numa unidade so)",
            {"grupo": c["group_id"], "url": (foto_url or "")[:120]},
        )
        return False
    try:
        import requests  # noqa: PLC0415
        payload = {
            "number": c["group_id"],
            "mediatype": "image",
            "media": foto_b64,
            "caption": legenda,
            "fileName": "oferta.jpg",
        }
        r = requests.post(
            f"{c['url']}/message/sendMedia/{c['instance']}",
            headers={"apikey": c["key"], "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        if r.status_code in (200, 201):
            log.info("✅ WA API: foto+legenda enviada para %s", c["group_id"])
            return True
        _registrar(
            "wa_api.envio_foto",
            f"sendMedia devolveu HTTP {r.status_code}: {r.text[:250]}",
            {"grupo": c["group_id"], "status": r.status_code},
        )
        return False
    except Exception as e:
        from core.error_logger import log_erro  # noqa: PLC0415
        log_erro("wa_api.envio_foto", e, {"grupo": c["group_id"], "url": foto_url[:80]})
        return False


def enviar_oferta_completa(produto: dict, mensagem: str) -> bool:
    """API pública: envia oferta com foto (se houver) + legenda completa.

    Chamada pelo whatsapp_sender.enviar_para_grupo() como tentativa 1.
    """
    if not _configurada():
        return False
    if not esta_conectada():
        # `esta_conectada()` ja registrou a causa exata (HTTP, estado da
        # instancia ou excecao). Registrar de novo aqui so duplicaria a
        # linha no bloco de notas da Area de Trabalho — dois registros para
        # uma falha so fazem o relatorio parecer duas vezes pior do que e.
        log.info("WA API: instancia nao conectada — nada enviado.")
        return False
    foto_url = produto.get("foto") or produto.get("imagem") or ""
    return enviar_foto_legenda(foto_url, mensagem)


def listar_grupos() -> list[dict]:
    """Lista os grupos do WhatsApp com seus JIDs (para configurar .env)."""
    c = _config()
    if not (c["url"] and c["key"]):
        return []
    try:
        import requests  # noqa: PLC0415
        r = requests.get(
            f"{c['url']}/group/fetchAllGroups/{c['instance']}?getParticipants=false",
            headers={"apikey": c["key"]},
            timeout=15,
        )
        if r.status_code != 200:
            _registrar(
                "wa_api.listar_grupos",
                f"fetchAllGroups devolveu HTTP {r.status_code}: {r.text[:200]}",
                {"instance": c["instance"], "status": r.status_code},
            )
            return []
        data = r.json()
        return [
            {"id": g.get("id"), "nome": g.get("subject", ""),
             "criador": g.get("owner", "")}
            for g in (data if isinstance(data, list) else data.get("groups", []))
        ]
    except Exception as e:
        from core.error_logger import log_erro  # noqa: PLC0415
        log_erro("wa_api.listar_grupos", e, {"instance": c["instance"]})
        return []
