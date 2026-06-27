# -*- coding: utf-8 -*-
"""
Autenticação OAuth2 com o Mercado Livre — obtém o access_token automaticamente.

Como usar:
    python ml_auth.py

O script:
  1. Abre o navegador na página de autorização do ML
  2. Aguarda você clicar em "Permitir"
  3. Captura o código automaticamente
  4. Troca pelo access_token e refresh_token
  5. Salva tudo no .env

Pré-requisito: adicione exatamente esta URL nas configurações do seu app ML:
    http://localhost:8888/callback
  → developers.mercadolivre.com.br → seu app → editar → Redirect URI
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

APP_ID     = os.getenv("ML_APP_ID", "")
APP_SECRET = os.getenv("ML_APP_SECRET", "")
REDIRECT   = "http://localhost:8888/callback"
ENV_FILE   = Path(__file__).parent / ".env"

_codigo_capturado: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silencia o log do servidor HTTP

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _codigo_capturado.append(params["code"][0])
            body = b"<h2>Autorizado! Pode fechar esta aba.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        else:
            erro = params.get("error", ["desconhecido"])[0]
            body = f"<h2>Erro: {erro}. Volte e tente novamente.</h2>".encode()
            self.send_response(400)
            self.end_headers()
            self.wfile.write(body)


def _trocar_codigo(codigo: str) -> dict:
    resp = requests.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            "grant_type":    "authorization_code",
            "client_id":     APP_ID,
            "client_secret": APP_SECRET,
            "code":          codigo,
            "redirect_uri":  REDIRECT,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    if not APP_ID or not APP_SECRET:
        print("❌ ML_APP_ID e ML_APP_SECRET precisam estar no .env")
        print("   Obtenha em: developers.mercadolivre.com.br")
        sys.exit(1)

    auth_url = (
        f"https://auth.mercadolivre.com.br/authorization"
        f"?response_type=code&client_id={APP_ID}&redirect_uri={urllib.parse.quote(REDIRECT)}"
    )

    print("=" * 55)
    print("Autenticação Mercado Livre")
    print("=" * 55)
    print()
    print("ANTES DE CONTINUAR — adicione esta URL no seu app ML:")
    print(f"  {REDIRECT}")
    print("  → developers.mercadolivre.com.br → seu app → editar → Redirect URI")
    print()
    input("Pressione ENTER quando tiver adicionado a URL acima...")

    print("\n🌐 Abrindo navegador para autorização...")
    webbrowser.open(auth_url)

    print("⏳ Aguardando sua autorização no navegador...")
    servidor = HTTPServer(("localhost", 8888), _Handler)
    servidor.handle_request()  # bloqueia até receber 1 request

    if not _codigo_capturado:
        print("❌ Não foi possível capturar o código. Tente novamente.")
        sys.exit(1)

    print("🔄 Trocando código pelo access_token...")
    try:
        tokens = _trocar_codigo(_codigo_capturado[0])
    except requests.HTTPError as e:
        print(f"❌ Erro ao obter token: {e}")
        sys.exit(1)

    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    user_id       = tokens.get("user_id", "")

    set_key(str(ENV_FILE), "ML_ACCESS_TOKEN",  access_token)
    set_key(str(ENV_FILE), "ML_REFRESH_TOKEN", refresh_token)
    set_key(str(ENV_FILE), "ML_USER_ID",       str(user_id))

    print()
    print("✅ Tokens salvos no .env com sucesso!")
    print(f"   User ID:  {user_id}")
    print(f"   Token:    {access_token[:30]}...")
    print()
    print("Agora rode: python rastreador.py")


if __name__ == "__main__":
    main()
