# -*- coding: utf-8 -*-
"""Setup direto — sem prompt de ENTER. Abre o browser e aguarda login."""
import sys, os, time, re
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\bot_ofertas")

_PROFILE_DIR = r"C:\bot_ofertas\data\ml_profile"
_MARKER_FILE = os.path.join(_PROFILE_DIR, ".logado")
_PORTAL_HUB  = "https://www.mercadolivre.com.br/afiliados/hub"
_PORTAL_GEN  = "https://www.mercadolivre.com.br/afiliados/tools/link-generator"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"

os.makedirs(_PROFILE_DIR, exist_ok=True)

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

print("=" * 55)
print("  SETUP PORTAL DE AFILIADOS ML")
print("=" * 55)
print()
print("Abrindo navegador... FAÇA LOGIN NO MERCADO LIVRE")
print("(use seu usuario e senha normalmente)")
print()

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        _PROFILE_DIR,
        headless=False,
        locale="pt-BR",
        user_agent=_UA,
        args=["--start-maximized", "--no-sandbox"],
        no_viewport=True,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(_PORTAL_HUB)
    print("Navegador aberto. Aguardando login no portal de afiliados...")
    print("(max 5 minutos)")
    print()

    logado = False
    for i in range(300):
        try:
            url = page.url
            # Confirmado quando está no portal de afiliados sem ser login
            if ("/afiliados/" in url and
                    "lgz" not in url and
                    "login" not in url.lower() and
                    "auth" not in url.lower()):
                # Aguarda carregar a página
                page.wait_for_load_state("networkidle", timeout=5000)
                # Verifica elemento que só existe quando logado
                el = page.query_selector("main, [class*='hub'], [class*='dashboard'], aside")
                if el:
                    print(f"\n✅ Login confirmado! URL: {url[:60]}")
                    logado = True
                    break
        except Exception:
            pass

        if i > 0 and i % 20 == 0:
            print(f"  Aguardando... {i}s - URL atual: {page.url[:50]}")
        time.sleep(1)

    if not logado:
        print("\n⏰ Timeout. Verifique se entrou no portal de afiliados.")

    if logado:
        # Salva marker
        with open(_MARKER_FILE, "w") as f:
            f.write("ok")
        print("\nTestando gerador de links...")
        try:
            page.goto(_PORTAL_GEN, wait_until="networkidle", timeout=20000)
            # Tenta preencher URL de teste
            url_teste = "https://www.mercadolivre.com.br/smartphone-motorola-moto-g86/p/MLB58684601"
            campo = None
            for sel in ['input[placeholder*="URL"]','input[placeholder*="Cole"]','input[type="url"]','.andes-form-control__field']:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        campo = el; break
                except: pass
            if campo:
                campo.fill(url_teste)
                page.keyboard.press("Enter")
                page.wait_for_timeout(5000)
                html = page.content()
                m = re.search(r"https?://meli\.la/[A-Za-z0-9]+", html)
                if m:
                    print(f"✅ Link meli.la gerado: {m.group(0)}")
                else:
                    print("⚠️  Gerador aberto mas link não foi extraído automaticamente.")
                    print("   O bot tentará gerar links meli.la ao publicar.")
            else:
                print("⚠️  Não encontrou campo do gerador de links.")
        except Exception as e:
            print(f"⚠️  Erro no teste: {e}")

    input("\nPressione ENTER para fechar o navegador...")
    ctx.close()

if logado:
    print("\n✅ Configuração concluída! Execute: python rastreador.py")
else:
    print("\n❌ Login não confirmado. Execute setup_ml.py novamente.")
