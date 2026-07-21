# -*- coding: utf-8 -*-
"""
Publicador unificado para múltiplas redes sociais.

Plataformas suportadas:
    - Telegram   ← já configurado (sempre ativo)
    - WhatsApp   ← via WHATSAPP_GROUP_ID + Evolution API ou pywhatkit
    - Instagram  ← via INSTAGRAM_USERNAME + INSTAGRAM_PASSWORD (instagrapi)
    - Twitter/X  ← via TWITTER_API_KEY + TWITTER_API_SECRET + tokens (tweepy)
    - Facebook   ← via FACEBOOK_PAGE_TOKEN + FACEBOOK_PAGE_ID (fb_messenger)

Variáveis .env necessárias por plataforma (todas opcionais exceto Telegram):

  # WhatsApp
  WHATSAPP_GROUP_ID=ABC123@g.us
  WHATSAPP_WEBHOOK_URL=http://localhost:8080   # Evolution API (opcional)
  WHATSAPP_API_KEY=sua_api_key                 # Evolution API (opcional)

  # Instagram
  INSTAGRAM_USERNAME=seu_usuario
  INSTAGRAM_PASSWORD=sua_senha

  # Twitter/X
  TWITTER_API_KEY=...
  TWITTER_API_SECRET=...
  TWITTER_ACCESS_TOKEN=...
  TWITTER_ACCESS_SECRET=...

  # Facebook
  FACEBOOK_PAGE_TOKEN=...
  FACEBOOK_PAGE_ID=...
"""
from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse

log = logging.getLogger(__name__)

_IG_USER   = os.getenv("INSTAGRAM_USERNAME", "")
_IG_PASS   = os.getenv("INSTAGRAM_PASSWORD", "")
_IG_SESSION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ig_session.json"
)
_ig_client = None
_TW_KEY    = os.getenv("TWITTER_API_KEY", "")
_TW_SECRET = os.getenv("TWITTER_API_SECRET", "")
_TW_TOKEN  = os.getenv("TWITTER_ACCESS_TOKEN", "")
_TW_TSECRET= os.getenv("TWITTER_ACCESS_SECRET", "")
_FB_TOKEN  = os.getenv("FACEBOOK_PAGE_TOKEN", "")
_FB_PAGE   = os.getenv("FACEBOOK_PAGE_ID", "")

# Instagram só permite 1 link clicável (bio e stories) — reaproveita a mesma
# página SITE_URL que o Telegram já usa, que reúne os botões de Telegram e
# WhatsApp (docs/index.html), em vez de forçar a escolha entre os dois grupos.
_LINK_BIO  = os.getenv("SITE_URL", "https://bot-ofertas.github.io/")


def plataformas_ativas() -> list[str]:
    ativas = ["telegram"]
    from integrations.whatsapp_sender import wa_ativo
    if wa_ativo():
        ativas.append("whatsapp")
    if _IG_USER and _IG_PASS:
        ativas.append("instagram")
    if _TW_KEY and _TW_TOKEN:
        ativas.append("twitter")
    if _FB_TOKEN and _FB_PAGE:
        ativas.append("facebook")
    return ativas


def _montar_texto_social(produto: dict, max_chars: int = 280) -> str:
    """Monta texto curto para redes sociais (Twitter tem limite de 280)."""
    titulo = (produto.get("titulo") or "")[:100]
    preco: float | None = produto.get("preco")
    preco_original: float | None = produto.get("preco_original")
    link: str = produto.get("link") or produto.get("affiliate_link") or ""
    cupom: str | None = produto.get("cupom")
    categoria: str = produto.get("categoria") or ""

    desc = ""
    if preco and preco_original and preco_original > preco:
        pct = int(round((1 - preco / preco_original) * 100))
        desc = f" -{pct}%"

    preco_txt = f"R${preco:.0f}{desc}" if preco else ""
    cupom_txt = f" | Cupom: {cupom}" if cupom else ""
    tags = f" #{categoria} #oferta #desconto" if categoria else " #oferta #desconto"

    base = f"🔥 {titulo} {preco_txt}{cupom_txt} 👉 {link}"
    if len(base + tags) <= max_chars:
        return base + tags
    return base[:max_chars - 3] + "..."


def _get_ig_client():
    """Cliente Instagram com sessão persistida em disco — login do zero a
    cada post é exatamente o padrão que o Instagram mais associa a bots e
    pode travar/banir a conta. Reaproveita a sessão salva sempre que possível
    e só faz login de verdade quando não há sessão válida ainda."""
    global _ig_client
    if _ig_client is not None:
        return _ig_client
    if not (_IG_USER and _IG_PASS):
        return None

    from instagrapi import Client  # noqa: PLC0415
    cl = Client()
    if os.path.exists(_IG_SESSION_PATH):
        try:
            cl.load_settings(_IG_SESSION_PATH)
            cl.login(_IG_USER, _IG_PASS)  # valida a sessão salva (barato se ainda válida)
            cl.get_timeline_feed()  # confirma que a sessão realmente funciona
            _ig_client = cl
            return _ig_client
        except Exception as e:
            log.info("Sessão Instagram salva não é mais válida (%s) — logando de novo", e)

    cl.login(_IG_USER, _IG_PASS)
    os.makedirs(os.path.dirname(_IG_SESSION_PATH), exist_ok=True)
    cl.dump_settings(_IG_SESSION_PATH)
    _ig_client = cl

    # Primeira sessão de verdade (sem arquivo salvo ainda) — aproveita pra
    # configurar o link da bio automaticamente, já que o usuário pediu que
    # isso fizesse parte da automação em vez de um passo manual à parte.
    try:
        cl.account_edit(external_url=_LINK_BIO)
        log.info("Bio do Instagram configurada automaticamente para: %s", _LINK_BIO)
    except Exception as e:
        log.info("Não deu pra configurar a bio automaticamente (%s) — use configurar_bio_link() manualmente", e)

    return _ig_client


def configurar_bio_link(url: str) -> bool:
    """Configura o link único da bio do Instagram (chamada única, manual —
    não faz parte do fluxo automático de publicação). Use para apontar a
    bio para uma página que reúne os links do Telegram/WhatsApp, já que o
    Instagram só permite 1 link clicável na bio."""
    cl = _get_ig_client()
    if cl is None:
        log.warning("Instagram não configurado — não é possível atualizar a bio")
        return False
    try:
        cl.account_edit(external_url=url)
        log.info("Bio do Instagram atualizada para: %s", url)
        return True
    except Exception as e:
        log.warning("Falha ao atualizar bio do Instagram: %s", e)
        return False


async def publicar_instagram_story(produto: dict, link_bio: str = "") -> bool:
    """Publica a oferta como Story (além do post normal). O Instagram só
    libera o sticker de link clicável em Stories a partir de uma certa
    maturidade/tamanho de conta — se não estiver disponível ainda, a foto
    é publicada mesmo assim, só sem o link."""
    cl = _get_ig_client()
    if cl is None:
        return False

    foto_url = produto.get("foto")
    if not foto_url:
        return False

    import pathlib
    import tempfile
    import requests as req  # noqa: PLC0415

    tmp_path = None
    try:
        from instagrapi.types import StoryLink  # noqa: PLC0415

        r = req.get(foto_url, timeout=10)
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(r.content)
            tmp_path = f.name

        links = [StoryLink(webUri=link_bio)] if link_bio else []
        cl.photo_upload_to_story(pathlib.Path(tmp_path), links=links)
        log.info("Instagram: story publicado para '%s'", (produto.get("titulo") or "")[:50])
        return True
    except ImportError:
        log.debug("instagrapi não instalado — pip install instagrapi")
        return False
    except Exception as e:
        log.warning("Instagram story falhou: %s", e)
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def publicar_instagram(produto: dict) -> bool:
    if not (_IG_USER and _IG_PASS):
        return False
    try:
        cl = _get_ig_client()
        if cl is None:
            return False
        titulo = produto.get("titulo") or "Oferta"
        preco: float | None = produto.get("preco")
        preco_original: float | None = produto.get("preco_original")
        link: str = produto.get("link") or produto.get("affiliate_link") or ""
        cupom: str | None = produto.get("cupom")
        categoria: str = produto.get("categoria") or "oferta"

        pct = ""
        if preco and preco_original and preco_original > preco:
            p = int(round((1 - preco / preco_original) * 100))
            pct = f"-{p}% OFF"

        caption = "\n".join(filter(None, [
            f"🔥 {titulo}",
            "",
            f"💰 R$ {preco:.2f} {pct}" if preco else "",
            f"🏷️ Cupom: {cupom}" if cupom else "",
            "",
            "🛡️ Oferta verificada · link na bio!",
            "",
            f"#{categoria} #oferta #desconto #mercadolivre #amazon #publicidade",
        ]))

        foto_url = produto.get("foto")
        if not foto_url:
            log.info("Instagram: sem foto para %s, pulando", titulo)
            return False

        import tempfile
        import requests as req  # noqa: PLC0415
        tmp_path = None
        try:
            r = req.get(foto_url, timeout=10)
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                f.write(r.content)
                tmp_path = f.name
            cl.photo_upload(tmp_path, caption=caption)
        finally:
            # Antes só apagava no caminho de sucesso — se photo_upload falhasse,
            # o .jpg temporário ficava órfão na pasta temp do sistema.
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        log.info("Instagram: publicado '%s'", titulo[:50])
        return True
    except ImportError:
        log.debug("instagrapi não instalado — pip install instagrapi")
    except Exception as e:
        log.warning("Instagram falhou: %s", e)
    return False


async def publicar_twitter(produto: dict) -> bool:
    if not (_TW_KEY and _TW_TOKEN):
        return False
    try:
        import tweepy  # noqa: PLC0415
        client = tweepy.Client(
            consumer_key=_TW_KEY,
            consumer_secret=_TW_SECRET,
            access_token=_TW_TOKEN,
            access_token_secret=_TW_TSECRET,
        )
        texto = _montar_texto_social(produto, max_chars=280)
        client.create_tweet(text=texto)
        log.info("Twitter/X: postado '%s'", produto.get("titulo", "")[:50])
        return True
    except ImportError:
        log.debug("tweepy não instalado — pip install tweepy")
    except Exception as e:
        log.warning("Twitter/X falhou: %s", e)
    return False


async def publicar_facebook(produto: dict) -> bool:
    if not (_FB_TOKEN and _FB_PAGE):
        return False
    try:
        import requests  # noqa: PLC0415
        titulo = produto.get("titulo") or "Oferta"
        preco: float | None = produto.get("preco")
        link: str = produto.get("link") or produto.get("affiliate_link") or ""
        cupom: str | None = produto.get("cupom")
        categoria: str = produto.get("categoria") or "oferta"

        msg = "\n".join(filter(None, [
            f"🔥 {titulo}",
            f"💰 R$ {preco:.2f}" if preco else "",
            f"🏷️ Cupom: {cupom}" if cupom else "",
            "",
            "🛡️ Oferta verificada",
            link,
            f"\n#{categoria} #oferta #desconto #publicidade",
        ]))

        r = requests.post(
            f"https://graph.facebook.com/{_FB_PAGE}/feed",
            data={"message": msg, "link": link, "access_token": _FB_TOKEN},
            timeout=10,
        )
        if r.status_code == 200:
            log.info("Facebook: postado '%s'", titulo[:50])
            return True
        log.warning("Facebook erro %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("Facebook falhou: %s", e)
    return False


async def publicar_todas_redes(produto: dict) -> dict[str, bool]:
    """Publica em todas as redes sociais configuradas (exceto Telegram e WhatsApp, já feitos pelo rastreador)."""
    resultados: dict[str, bool] = {}

    tasks = []
    nomes = []

    if _IG_USER and _IG_PASS:
        tasks.append(publicar_instagram(produto))
        nomes.append("instagram")
        tasks.append(publicar_instagram_story(produto, link_bio=_LINK_BIO))
        nomes.append("instagram_story")
    if _TW_KEY and _TW_TOKEN:
        tasks.append(publicar_twitter(produto))
        nomes.append("twitter")
    if _FB_TOKEN and _FB_PAGE:
        tasks.append(publicar_facebook(produto))
        nomes.append("facebook")

    if tasks:
        resultados_async = await asyncio.gather(*tasks, return_exceptions=True)
        for nome, res in zip(nomes, resultados_async):
            resultados[nome] = res is True

    return resultados


def resumo_redes(resultados: dict[str, bool]) -> str:
    if not resultados:
        return ""
    ok = [k for k, v in resultados.items() if v]
    fail = [k for k, v in resultados.items() if not v]
    partes = []
    if ok:
        partes.append(f"✅ {', '.join(ok)}")
    if fail:
        partes.append(f"❌ {', '.join(fail)}")
    return " | ".join(partes)
