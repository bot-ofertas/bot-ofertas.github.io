# -*- coding: utf-8 -*-
"""
SETUP DA WHATSAPP API (Evolution API)
=====================================
Guia interativo para conectar o bot à Evolution API:

  1. Sobe o container Docker (evolution-api)
  2. Cria a instância "botofertas"
  3. Exibe o QR Code para escanear no celular
  4. Lista grupos do usuário e ajuda a preencher o WHATSAPP_GROUP_ID
  5. Testa envio no grupo

Uso:
    python setup_whatsapp_api.py [passo]
    passo: start | qr | groups | test  (padrão: guiado interativo)
"""
import base64
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
API_URL = os.getenv("WHATSAPP_WEBHOOK_URL", "http://localhost:8080")
API_KEY = os.getenv("WHATSAPP_API_KEY", "b6d3f7c9e2a48b5f7c1e9d2a4b8f5e7c")
INSTANCE = os.getenv("WHATSAPP_INSTANCE", "botofertas")


def _req(method: str, path: str, body: dict | None = None, timeout: int = 15):
    import requests  # type: ignore
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    fn = getattr(requests, method.lower())
    kwargs = {"headers": headers, "timeout": timeout}
    if body is not None:
        kwargs["json"] = body
    return fn(f"{API_URL}{path}", **kwargs)


def start_docker() -> bool:
    print("=" * 60)
    print("  Passo 1/4 — Subindo container da Evolution API")
    print("=" * 60)
    yml = os.path.join(BASE, "docker", "evolution.yml")
    if not os.path.exists(yml):
        print(f"❌ {yml} não encontrado")
        return False
    try:
        subprocess.run(
            ["docker", "compose", "-f", yml, "up", "-d"],
            check=True, cwd=BASE,
        )
    except FileNotFoundError:
        print("❌ Docker não instalado. Instale Docker Desktop e tente de novo.")
        return False
    except subprocess.CalledProcessError as e:
        print(f"❌ Falha ao subir container: {e}")
        return False
    print("✅ Container em execução em http://localhost:8080")

    # Aguarda API responder
    print("⏳ Aguardando API responder...")
    for _ in range(30):
        try:
            import requests  # type: ignore
            r = requests.get(f"{API_URL}/", timeout=2)
            if r.status_code < 500:
                print("✅ API respondeu")
                return True
        except Exception:
            pass
        time.sleep(2)
    print("⚠️  API não respondeu em 60s — continue manualmente")
    return False


def criar_instancia() -> bool:
    print("=" * 60)
    print("  Passo 2/4 — Criando instância botofertas")
    print("=" * 60)
    r = _req("POST", "/instance/create", {
        "instanceName": INSTANCE,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
    })
    if r.status_code in (200, 201):
        d = r.json()
        print(f"✅ Instância '{INSTANCE}' criada")
        # Grava a apikey retornada, se existir
        api_key = d.get("hash", {}).get("apikey") or d.get("hash") or API_KEY
        if isinstance(api_key, str) and api_key != API_KEY:
            print(f"⚠️  apikey da instância: {api_key}")
            print(f"   Atualize WHATSAPP_API_KEY no .env")
        return True
    if r.status_code == 403 and "already" in r.text.lower():
        print(f"ℹ️  Instância '{INSTANCE}' já existe — usando ela")
        return True
    print(f"❌ Erro {r.status_code}: {r.text[:300]}")
    return False


def mostrar_qr() -> bool:
    print("=" * 60)
    print("  Passo 3/4 — QR Code para escanear")
    print("=" * 60)
    r = _req("GET", f"/instance/connect/{INSTANCE}")
    if r.status_code != 200:
        print(f"❌ Erro {r.status_code}: {r.text[:200]}")
        return False
    d = r.json()
    b64 = d.get("base64") or d.get("qrcode", {}).get("base64", "")
    if not b64:
        pairing = d.get("pairingCode") or d.get("code")
        if pairing:
            print(f"ℹ️  Código de pareamento: {pairing}")
            print("   No celular: WhatsApp > Aparelhos conectados > Conectar > Usar código")
        else:
            print("⚠️  Sem QR nem código — talvez já esteja conectado")
        return True

    # Salva o QR como imagem
    qr_path = os.path.join(BASE, "data", "qr_evolution.png")
    os.makedirs(os.path.dirname(qr_path), exist_ok=True)
    with open(qr_path, "wb") as f:
        f.write(base64.b64decode(b64.split(",")[-1]))
    print(f"✅ QR salvo em: {qr_path}")
    print("   Abra o arquivo e escaneie no celular:")
    print("   WhatsApp > ⋮ > Aparelhos conectados > Conectar um aparelho")
    print("\n⏳ Aguardando conexão...")
    for i in range(60):
        time.sleep(2)
        try:
            s = _req("GET", f"/instance/connectionState/{INSTANCE}")
            state = s.json().get("instance", {}).get("state", "")
            if state == "open":
                print("✅ CONECTADO! WhatsApp vinculado à API.")
                return True
        except Exception:
            pass
    print("⚠️  Timeout aguardando conexão. Rode novamente se necessário.")
    return False


def listar_grupos_e_orientar() -> bool:
    print("=" * 60)
    print("  Passo 4/4 — Grupos disponíveis")
    print("=" * 60)
    r = _req("GET", f"/group/fetchAllGroups/{INSTANCE}?getParticipants=false")
    if r.status_code != 200:
        print(f"❌ Erro {r.status_code}: {r.text[:200]}")
        return False
    data = r.json()
    grupos = data if isinstance(data, list) else data.get("groups", [])
    if not grupos:
        print("⚠️  Nenhum grupo encontrado (você precisa participar de algum)")
        return False
    print(f"\nEncontrei {len(grupos)} grupo(s). JIDs para colar no .env:\n")
    for i, g in enumerate(grupos[:30], 1):
        jid = g.get("id", "")
        nome = g.get("subject", "sem nome")
        print(f"  {i:2d}. {nome[:50]:50s}  →  {jid}")
    print("\n📝 No .env, defina:")
    print(f"   WHATSAPP_WEBHOOK_URL={API_URL}")
    print(f"   WHATSAPP_API_KEY={API_KEY}")
    print(f"   WHATSAPP_INSTANCE={INSTANCE}")
    print("   WHATSAPP_GROUP_ID=<cole o JID do grupo acima>")
    return True


def testar_envio() -> bool:
    from integrations.whatsapp_api import enviar_texto, esta_conectada
    if not esta_conectada():
        print("❌ Instância não está conectada.")
        return False
    ok = enviar_texto("🤖 Teste de conexão do bot com a Evolution API — se você vê essa mensagem, tudo certo!")
    print("✅ Enviado" if ok else "❌ Falhou")
    return ok


def main():
    passo = sys.argv[1] if len(sys.argv) > 1 else "all"
    if passo in ("start", "all"):
        if not start_docker():
            return
        criar_instancia()
    if passo in ("qr", "all"):
        mostrar_qr()
    if passo in ("groups", "all"):
        listar_grupos_e_orientar()
    if passo == "test":
        testar_envio()


if __name__ == "__main__":
    main()
