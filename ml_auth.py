# -*- coding: utf-8 -*-
"""
Autenticação com o Mercado Livre via Client Credentials.
Não precisa de redirect URI nem de navegador — funciona direto.

Como usar:
    python ml_auth.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

APP_ID     = os.getenv("ML_APP_ID", "")
APP_SECRET = os.getenv("ML_APP_SECRET", "")
ENV_FILE   = Path(__file__).parent / ".env"


def main():
    if not APP_ID or not APP_SECRET:
        print("❌ ML_APP_ID e ML_APP_SECRET precisam estar no .env")
        sys.exit(1)

    print("Obtendo token do Mercado Livre...")
    try:
        resp = requests.post(
            "https://api.mercadolibre.com/oauth/token",
            data={
                "grant_type":    "client_credentials",
                "client_id":     APP_ID,
                "client_secret": APP_SECRET,
            },
            timeout=15,
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        print(f"❌ Erro: {e}")
        print(f"   Resposta: {resp.text}")
        sys.exit(1)

    token = resp.json().get("access_token", "")
    if not token:
        print(f"❌ Token não retornado: {resp.json()}")
        sys.exit(1)

    set_key(str(ENV_FILE), "ML_ACCESS_TOKEN", token)

    print(f"✅ Token obtido e salvo no .env!")
    print(f"   {token[:40]}...")
    print()
    print("Agora rode: python rastreador.py")


if __name__ == "__main__":
    main()
