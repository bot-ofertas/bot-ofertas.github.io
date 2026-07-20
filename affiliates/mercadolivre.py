# -*- coding: utf-8 -*-
"""
Provedor de links de afiliado do Mercado Livre.

Usa perfil persistente do Playwright (data/ml_profile/) para manter sessão
sem precisar de cookies ou login repetido.

Setup (uma única vez):
    python -m affiliates.mercadolivre setup
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

from affiliates.base import AffiliateProvider

_DIR          = os.path.dirname(os.path.abspath(__file__))
_PROFILE_DIR  = os.path.join(_DIR, "..", "data", "ml_profile")   # sessão persistente
_MARKER_FILE  = os.path.join(_PROFILE_DIR, ".logado")             # marca que o login foi feito

_PORTAL_GERADOR = "https://www.mercadolivre.com.br/afiliados/tools/link-generator"
_PORTAL_HUB     = "https://www.mercadolivre.com.br/afiliados/hub"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_SELETORES_INPUT = [
    'input[placeholder*="URL"]',
    'input[placeholder*="url"]',
    'input[placeholder*="Link"]',
    'input[placeholder*="link"]',
    'input[placeholder*="Cole"]',
    'input[placeholder*="Informe"]',
    'input[type="url"]',
    'input[name="url"]',
    'input[name="link"]',
    '.andes-form-control__field',
    'input[class*="input"]',
]

_SELETORES_BOTAO = [
    'button[type="submit"]',
    'button:has-text("Gerar")',
    'button:has-text("Criar")',
    'button:has-text("Obter")',
    'button[class*="generate"]',
    'button[class*="submit"]',
]


class MLAffiliateProvider(AffiliateProvider):
    name = "mercadolivre"
    # "or" (não default do getenv) cobre tanto var ausente quanto var vazia —
    # um secret configurado como string vazia (ex: GitHub Secret removido)
    # não pode resultar num matt_tool= vazio no link publicado.
    _TOOL_ID = os.getenv("ML_AFFILIATE_TOOL_ID") or "47114387"
    _TOOL_ID_ESPERADO = "47114387"  # valor fixo p/ validação — nunca confia em _TOOL_ID sozinho

    # ── Contrato público ───────────────────────────────────────────────────────

    def can_handle(self, url: str) -> bool:
        return "mercadolivre.com.br" in url or "meli.la" in url

    def validate_affiliate_link(self, link: str) -> bool:
        if not link:
            return False
        # Valida contra o ID esperado FIXO, não contra self._TOOL_ID — se o
        # env var vier corrompido/vazio por algum motivo, a validação não
        # pode "se enganar sozinha" comparando o link contra o próprio valor
        # quebrado e deixando passar um link sem o afiliado de verdade.
        return "meli.la/" in link or f"matt_tool={self._TOOL_ID_ESPERADO}" in link

    def health_check(self) -> bool:
        return os.path.exists(_MARKER_FILE)

    def _link_direto_com_afiliado(self, url: str) -> str:
        url_base = url.split("?")[0].rstrip("/")
        return f"{url_base}?matt_tool={self._TOOL_ID}&matt_source=bot_telegram"

    # ── Geração de link ────────────────────────────────────────────────────────

    def generate_affiliate_link(self, url: str) -> str | None:
        if not self.health_check():
            return self._link_direto_com_afiliado(url)
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                _PROFILE_DIR, headless=True, locale="pt-BR", user_agent=_UA,
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            link = self._gerar_link_pagina(page, url)
            ctx.close()
        return link or self._link_direto_com_afiliado(url)

    async def generate_affiliate_link_async(self, url: str) -> str | None:
        if not self.health_check():
            return self._link_direto_com_afiliado(url)
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                _PROFILE_DIR, headless=True, locale="pt-BR", user_agent=_UA,
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            links_api: list[str] = []

            async def on_response(resp):
                if resp.status == 200 and any(k in resp.url for k in ("link", "short", "afiliado")):
                    try:
                        body = await resp.json()
                        found = self._extrair_melila_json(body)
                        if found:
                            links_api.append(found)
                    except Exception:
                        pass

            page.on("response", on_response)

            try:
                await page.goto(_PORTAL_GERADOR, wait_until="networkidle", timeout=30000)
            except PWTimeout:
                await page.goto(_PORTAL_GERADOR, wait_until="domcontentloaded", timeout=20000)

            if "login" in page.url or "lgz" in page.url:
                await ctx.close()
                # Sessão expirou — limpa marker para forçar novo setup
                if os.path.exists(_MARKER_FILE):
                    os.remove(_MARKER_FILE)
                print("  ⚠️  Sessão ML expirada — usando link direto (execute setup novamente)")
                return self._link_direto_com_afiliado(url)

            link = await self._preencher_e_gerar_async(page, url, links_api)
            await ctx.close()

        return link or self._link_direto_com_afiliado(url)

    # ── Setup (uma única vez) ──────────────────────────────────────────────────

    @staticmethod
    def setup() -> None:
        from playwright.sync_api import sync_playwright

        os.makedirs(_PROFILE_DIR, exist_ok=True)

        print("\n" + "=" * 55)
        print("  CONFIGURAÇÃO DO PORTAL DE AFILIADOS ML")
        print("=" * 55)
        print()
        print("Um navegador vai abrir. Faça login com sua conta ML.")
        print("Após entrar no portal de afiliados, aguarde a confirmação.")
        print()
        input("Pressione ENTER para abrir o navegador...")

        with sync_playwright() as p:
            # Perfil persistente — mantém sessão para sempre
            ctx = p.chromium.launch_persistent_context(
                _PROFILE_DIR,
                headless=False,
                locale="pt-BR",
                user_agent=_UA,
                args=["--start-maximized"],
                no_viewport=True,
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(_PORTAL_HUB)

            print("\nFaça login com seu usuário e senha do Mercado Livre.")
            print("Aguardando você entrar no portal de afiliados...\n")

            logado = False
            for i in range(300):  # até 5 minutos
                try:
                    url = page.url
                    # Confirmado logado quando estiver numa página do portal sem login
                    if ("/afiliados/" in url and
                            "lgz" not in url and
                            "login" not in url.lower() and
                            "auth" not in url.lower()):
                        # Verifica elemento da página que só existe quando logado
                        el = page.query_selector("aside, nav, [class*='hub'], [class*='dashboard'], main")
                        if el:
                            logado = True
                            break
                except Exception:
                    pass
                if i > 0 and i % 15 == 0:
                    print(f"  Aguardando login... ({i}s)")
                time.sleep(1)

            if logado:
                print("\n✅ Login detectado!")
                # Marca sessão como válida
                with open(_MARKER_FILE, "w") as f:
                    f.write("ok")

                # Testa geração de link
                print("Testando geração de link meli.la no portal...")
                try:
                    page.goto(_PORTAL_GERADOR, wait_until="networkidle", timeout=20000)
                    url_teste = "https://www.mercadolivre.com.br/smartphone-samsung-galaxy-a36/p/MLB123"
                    link = MLAffiliateProvider._gerar_link_pagina(page, url_teste)
                    if link and "meli.la" in link:
                        print(f"✅ Link meli.la gerado com sucesso: {link}")
                    else:
                        print("⚠️  Link gerado mas não é meli.la. Verifique o gerador de links.")
                except Exception as e:
                    print(f"⚠️  Erro no teste: {e}")
            else:
                print("\n⚠️  Tempo esgotado. Execute setup novamente e faça login mais rápido.")

            ctx.close()

        if logado:
            print("\n✅ Configuração concluída! O bot usará links meli.la.")
        else:
            print("\n❌ Login não detectado. Execute setup novamente.")

    # ── Auxiliares ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extrair_melila_json(obj, depth: int = 0) -> str | None:
        if depth > 6:
            return None
        if isinstance(obj, dict):
            for k in ("short_url", "link", "url", "meli_link", "short_link", "affiliate_link"):
                v = obj.get(k, "")
                if isinstance(v, str) and "meli.la" in v:
                    return v
            for v in obj.values():
                r = MLAffiliateProvider._extrair_melila_json(v, depth + 1)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = MLAffiliateProvider._extrair_melila_json(item, depth + 1)
                if r:
                    return r
        elif isinstance(obj, str) and "meli.la" in obj:
            m = re.search(r"https?://meli\.la/[A-Za-z0-9]+", obj)
            if m:
                return m.group(0)
        return None

    @staticmethod
    def _extrair_melila_dom(page) -> str | None:
        m = re.search(r"https?://meli\.la/[A-Za-z0-9]+", page.content())
        return m.group(0) if m else None

    @staticmethod
    async def _extrair_melila_dom_async(page) -> str | None:
        m = re.search(r"https?://meli\.la/[A-Za-z0-9]+", await page.content())
        return m.group(0) if m else None

    @staticmethod
    def _gerar_link_pagina(page, url_produto: str) -> str | None:
        from playwright.sync_api import TimeoutError as PWTimeout
        links = []

        def on_resp(r):
            if r.status == 200:
                try:
                    b = r.json()
                    f = MLAffiliateProvider._extrair_melila_json(b)
                    if f:
                        links.append(f)
                except Exception:
                    pass
        page.on("response", on_resp)

        campo = None
        for sel in _SELETORES_INPUT:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    campo = el
                    break
            except Exception:
                pass

        if campo:
            campo.fill(url_produto)
            page.keyboard.press("Enter")
            for sel in _SELETORES_BOTAO:
                try:
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.click()
                        break
                except Exception:
                    pass
            try:
                page.wait_for_selector('input[value*="meli.la"], a[href*="meli.la"]', timeout=12000)
            except PWTimeout:
                pass
            page.wait_for_timeout(2000)

        if links:
            return links[0]
        return MLAffiliateProvider._extrair_melila_dom(page)

    async def _preencher_e_gerar_async(self, page, url_produto: str, links_api: list) -> str | None:
        from playwright.async_api import TimeoutError as PWTimeout

        campo = None
        for sel in _SELETORES_INPUT:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    campo = el
                    break
            except Exception:
                pass

        if campo:
            await campo.fill(url_produto)
            await page.keyboard.press("Enter")
            for sel in _SELETORES_BOTAO:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        break
                except Exception:
                    pass
            try:
                await page.wait_for_selector('input[value*="meli.la"], a[href*="meli.la"]', timeout=12000)
            except PWTimeout:
                pass
            await page.wait_for_timeout(2000)

        if links_api:
            return links_api[0]
        return await self._extrair_melila_dom_async(page)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        MLAffiliateProvider.setup()
    else:
        print("Uso: python -m affiliates.mercadolivre setup")
