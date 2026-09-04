#!/usr/bin/env bash
# botctl — o dia a dia do bot no servidor.
#
#   bash deploy/botctl.sh <comando>
#
#   status      o que está de pé, qual o papel, saúde do /health
#   qr          conecta o WhatsApp (mostra o QR para ler no celular)
#   grupos      lista os grupos e os IDs, para preencher WHATSAPP_GROUP_ID
#   logs [srv]  logs ao vivo (padrão: todos)
#   reiniciar   relê o .env e reinicia os 4 processos
#   parar       derruba tudo (os volumes ficam: banco e sessão sobrevivem)
#   atualizar   puxa o código do GitHub e reconstrói só se algo mudou
#   site        regenera docs/ a partir do banco e publica no GitHub Pages
#   papel       mostra se este servidor pode publicar agora, e por quê
#
# Nenhum comando aqui pede que você digite a chave da Evolution: eles leem
# do .env e mandam por stdin do curl, para a chave não sobrar no histórico
# do shell nem aparecer num `ps`.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_comum.sh"

cmd="${1:-status}"; shift || true
exigir_env

case "$cmd" in

status)
    echo "── containers ───────────────────────────────────────────"
    dc ps
    echo
    echo "── papel / saúde ────────────────────────────────────────"
    if curl -sf --max-time 5 http://127.0.0.1:8724/health -o /tmp/health.$$ ; then
        python3 - /tmp/health.$$ <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
p = d.get("papel", {})
print(f"papel        : {p.get('papel','?')}")
print(f"pode publicar: {p.get('pode_publicar')}  ({p.get('motivo','')})")
if p.get("fuso_ok") is False:
    print(f"  !! FUSO ERRADO ({p.get('utc_offset')}): a janela do ciclo escorrega."
          "\n     Confira TZ=America/Sao_Paulo no .env e reinicie.")
for chave in ("telegram", "whatsapp", "rastreador"):
    c = d.get(chave, {})
    print(f"{chave:<13}: {'ok' if c.get('ok') else 'ATENCAO'}  {c.get('detalhe', c.get('motivo',''))}")
u = d.get("ultimo_post", {})
if u:
    print(f"ultimo post  : {u.get('quando', u)}")
PY
        rm -f /tmp/health.$$
    else
        echo "healthcheck não respondeu em 127.0.0.1:8724 — veja: botctl.sh logs rastreador"
    fi
    ;;

qr)
    INST="$(instancia)"
    DEST="${HOME:-/root}/qr_whatsapp.png"
    RESP="$(evo GET "/instance/connect/$INST")"
    B64="$(printf '%s' "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("base64",""))' 2>/dev/null || true)"
    CODE="$(printf '%s' "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("code",""))' 2>/dev/null || true)"

    if [[ -z "$B64" && -z "$CODE" ]]; then
        echo "A Evolution não devolveu QR. Ou já está conectada, ou não subiu:"
        evo GET "/instance/connectionState/$INST"; echo
        exit 1
    fi

    if command -v qrencode &>/dev/null && [[ -n "$CODE" ]]; then
        echo "Leia no celular: WhatsApp → Aparelhos conectados → Conectar aparelho"
        echo
        qrencode -t ANSIUTF8 "$CODE"
    else
        printf '%s' "$B64" | sed 's#^data:image/png;base64,##' | base64 -d > "$DEST" 2>/dev/null || true
        echo "QR salvo em: $DEST"
        echo
        echo "Para ver o QR direto neste terminal (mais fácil):"
        echo "    apt-get install -y qrencode && bash deploy/botctl.sh qr"
        echo
        echo "Ou baixe o arquivo para o seu computador:"
        echo "    scp root@SEU_IP:$DEST ."
    fi
    echo
    echo "Depois de ler, confira:  bash deploy/botctl.sh grupos"
    ;;

grupos)
    INST="$(instancia)"
    # A resposta vai para um arquivo em vez de um pipe: o script Python
    # precisa vir num heredoc (para poder usar aspas simples e duplas sem
    # escapar), e nesse caso o stdin dele e o proprio heredoc.
    RESP_FILE="$(mktemp)"; trap 'rm -f "$RESP_FILE"' EXIT
    evo GET "/group/fetchAllGroups/$INST?getParticipants=false" > "$RESP_FILE"
    python3 - "$RESP_FILE" <<'PYGRUPOS'
import json, sys
try:
    dados = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("Resposta inesperada da Evolution. O WhatsApp ja esta conectado?")
    print("   bash deploy/botctl.sh qr")
    raise SystemExit(1)
if isinstance(dados, dict):
    # Erro da API vem como objeto ({"status":401,...}); a lista vem como array.
    print(json.dumps(dados, ensure_ascii=False)[:300])
    raise SystemExit(1)
for g in dados:
    print("{:<28} {}".format(g.get("id", ""), g.get("subject", "")))
PYGRUPOS
    echo
    echo "Cole o ID do grupo certo em WHATSAPP_GROUP_ID no .env e rode:"
    echo "    bash deploy/botctl.sh reiniciar"
    ;;

logs)     dc logs -f --tail 100 "$@" ;;
reiniciar) dc up -d --force-recreate rastreador rastreador_amazon campanha_ferramentas whatsapp_queue ;;
parar)    dc down ;;

papel)
    dc exec -T rastreador python -m core.papel
    ;;

atualizar) exec bash "$DEPLOY_DIR/atualizar.sh" ;;
site)      exec bash "$DEPLOY_DIR/publicar_site.sh" ;;

*)
    sed -n '2,20p' "${BASH_SOURCE[0]}"
    exit 1
    ;;
esac
