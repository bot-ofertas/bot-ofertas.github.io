# O bot na nuvem (DigitalOcean)

Guia para tirar o bot da dependência do PC ligado. O que muda, o que
continua igual, e o que só você pode fazer.

---

## O que muda quando o bot sai do PC

**O WhatsApp muda de método.** No PC, o envio é automação da janela do
WhatsApp Desktop que você já tem aberto e logado. Num servidor Linux não
existe WhatsApp Desktop nem tela — esse caminho simplesmente não roda.
No servidor quem fala com o WhatsApp é a **Evolution API**, um container
que mantém a sessão do seu número (é o mesmo tipo de sessão de
"aparelho conectado" do WhatsApp Web). Ela sobe junto e o código já sabe
usá-la: `integrations/whatsapp_api.py` é a primeira tentativa de envio,
antes de qualquer caminho de Windows.

Isso exige **um QR Code lido no seu celular, uma vez**. Não dá para
automatizar: é o WhatsApp autorizando um aparelho novo, e a autorização é
sua. Depois disso a sessão fica no volume Docker e sobrevive a reinício,
atualização e reboot do servidor.

**O Telegram não muda em nada.** Mesmo token, mesmo canal, mesmo formato.

**O site continua saindo do GitHub Pages**, mas quem gera e publica passa a
ser o servidor — ver "Site" mais abaixo.

---

## O problema que vem junto: dois bots publicando

Passam a existir três coisas capazes de postar no mesmo canal do Telegram e
no mesmo grupo do WhatsApp:

| Publicador | Quando rodava até agora |
|---|---|
| O PC do Daniel | 08:30 → 02:00 |
| GitHub Actions (`.github/workflows/bot.yml`) | de madrugada, com o PC desligado |
| **O servidor** | 24h, se ninguém disser o contrário |

E cada um tem o **seu próprio banco de deduplicação** — o do PC está no
disco dele, o do Actions é um cache do runner, o do servidor é um volume
Docker. Nenhum enxerga o que o outro publicou. Dois rodando ao mesmo tempo
não é o dobro de ofertas: é a **mesma** oferta saindo duas vezes no grupo.

Quem resolve isso é `core/papel.py`, e a única coisa que ele precisa saber é
**quem é esta instância** — a variável `PAPEL` no `.env`:

| `PAPEL` | Comportamento |
|---|---|
| `local` (padrão quando a variável não existe) | Sem trava. É o PC. |
| `nuvem` | Publica **só** enquanto o PC não pode estar publicando (fora de 08:30–02:00, mais 35 min de carência do desligamento). |
| `nuvem-exclusiva` | Publica 24h. Só depois de calar o PC e o Actions. |
| `desligado` | Não publica. |

O servidor nasce em `nuvem`: sobe hoje e no primeiro dia já não pisa no PC.
Quando você quiser que o servidor seja o único publicador, são três
mudanças — as três na seção "Virar a chave", no fim.

Para conferir a qualquer momento:

```bash
bash deploy/botctl.sh papel
```

---

## Instalação

### 1. Crie o droplet

No DigitalOcean: **Create → Droplets**.

| Campo | Escolha |
|---|---|
| Região | New York ou São Paulo (mais perto = menos latência com o ML) |
| Imagem | Ubuntu 24.04 LTS |
| Tipo | Basic → Regular → **2 GB RAM / 1 vCPU / 50 GB** |
| Autenticação | **SSH Key** (não senha) |

Sobre o tamanho: o de 1 GB (US$ 6) não dá conta. O Chromium do Playwright
sozinho passa de 500 MB de RAM em pico, e ao lado dele rodam a Evolution
(Node) e quatro processos Python. Com 1 GB o kernel começa a matar processo
no meio da rodada, e o sintoma é o bot "parar de publicar sem erro". O de
2 GB (US$ 12/mês na data desta escrita — confira o preço atual, eu não
tenho acesso à sua conta) é o primeiro que fecha com folga.

### 2. Entre e traga o repositório

```bash
ssh root@SEU_IP

apt update && apt install -y git
git clone https://github.com/bot-ofertas/bot-ofertas.github.io.git
cd bot-ofertas.github.io
```

### 3. Monte o `.env`

O `.env` do servidor é o da raiz **mais** um bloco específico da nuvem. Junte
os dois nessa ordem (o que vem depois vence):

```bash
cat .env.example deploy/.env.example > .env
nano .env
```

Preencha:

- `TOKEN_TELEGRAM`, `CANAL_GERAL` — iguais aos do PC
- `ML_APP_ID`, `ML_APP_SECRET` — iguais aos do PC
- `AMAZON_AFFILIATE_TAG` — igual ao do PC
- `EVOLUTION_API_KEY` — **nova**, aleatória. Gere com:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
  Essa chave dá poder de enviar mensagem pelo seu número. Trate como senha.
- `WHATSAPP_GROUP_ID` — **deixe vazio por enquanto**. Você só descobre o ID
  com a pilha no ar (passo 6). Vazio significa WhatsApp desligado e Telegram
  publicando normalmente.

### 4. Suba

```bash
sudo bash deploy/deploy_vps.sh
```

Instala Docker se faltar, constrói a imagem, sobe os cinco containers
(Evolution + os quatro processos do bot) e cria a instância do WhatsApp.
Pode rodar de novo quantas vezes quiser: não apaga volume, então o banco de
deduplicação e a sessão do WhatsApp sobrevivem.

**A partir daqui o Telegram já está publicando.**

### 5. Conecte o WhatsApp (o QR)

```bash
apt-get install -y qrencode        # opcional, mas mostra o QR no terminal
bash deploy/botctl.sh qr
```

No celular: **WhatsApp → Aparelhos conectados → Conectar aparelho**, e leia
o QR. Sem o `qrencode`, o comando salva um PNG e mostra como baixá-lo.

Uma vez só. A sessão fica no volume `evolution_data`.

### 6. Descubra o ID do grupo

```bash
bash deploy/botctl.sh grupos
```

Sai uma lista de `120363...@g.us` com o nome de cada grupo. Copie o do grupo
de ofertas, cole em `WHATSAPP_GROUP_ID` no `.env` e reinicie:

```bash
nano .env
bash deploy/botctl.sh reiniciar
```

### 7. Confira

```bash
bash deploy/botctl.sh status
```

Mostra os containers de pé, o papel deste servidor, se ele pode publicar
agora e por quê, e a saúde de Telegram/WhatsApp/rastreador.

Se `papel.fuso_ok` vier `false`, o relógio do servidor não está em horário
de Brasília e a janela do ciclo escorrega — confira `TZ=America/Sao_Paulo`
no `.env`.

---

## GitHub: o servidor segue o repositório

O repositório continua sendo a fonte da verdade. O servidor **puxa**; o
GitHub não abre conexão para cá. Assim não existe chave de acesso ao
servidor guardada no GitHub, nada precisa ser aberto no firewall, e trocar
o IP do droplet não quebra nada.

```bash
bash deploy/botctl.sh atualizar     # busca, reconstrói só se mudou
```

Para isso rodar sozinho de hora em hora:

```bash
sudo bash deploy/instalar_timers.sh
```

Instala dois temporizadores do systemd: um que atualiza o código, outro que
publica o site.

### Site (GitHub Pages)

O site sai de `export_json.py`, que lê o banco. No servidor o banco está
dentro de um volume Docker, e o publicador que roda **dentro** do container
não tem `.git` nenhum para commitar — a imagem não carrega credencial de
push, de propósito. Então o container só gera (escreve em `docs/`, que é uma
pasta compartilhada com o servidor) e quem commita e empurra é o servidor:

```bash
bash deploy/botctl.sh site
```

Para o servidor conseguir empurrar, ele precisa de uma chave de deploy:

```bash
bash deploy/configurar_git_deploy.sh
```

O script gera um par de chaves **aqui no servidor**. A parte privada nunca
sai deste disco. A parte pública é impressa no fim, e é ela que você cola em
**Settings → Deploy keys → Add deploy key**, marcando **Allow write access**.
Nenhuma senha é digitada em lugar nenhum.

---

## Segurança

**Nada fica exposto na internet.** As duas portas (8080 da Evolution, 8724 do
healthcheck) são publicadas só no loopback do servidor. A 8080 serve o painel
`/manager`, que autentica com a mesma chave que envia mensagem pelo seu
número — aberta na internet, seria um painel de controle do seu WhatsApp à
disposição de quem achasse o IP.

Para abrir o painel ou o healthcheck do seu computador, faça um túnel SSH:

```bash
ssh -L 8080:127.0.0.1:8080 -L 8724:127.0.0.1:8724 root@SEU_IP
```

E acesse `http://localhost:8080/manager` e `http://localhost:8724/health` no
seu navegador, como se fossem locais.

Firewall recomendado no droplet (deixa só o SSH entrar):

```bash
ufw allow OpenSSH && ufw --force enable
```

---

## Virar a chave: o servidor como único publicador

Enquanto o servidor está em `nuvem`, ele publica só de madrugada. Para ele
assumir o dia inteiro, as três coisas abaixo — **as três**, senão volta a
oferta duplicada:

1. **No servidor:** `PAPEL=nuvem-exclusiva` no `.env`, depois
   `bash deploy/botctl.sh reiniciar`.
2. **No GitHub:** Settings → Secrets and variables → Actions → Variables →
   `PAPEL` = `desligado`. O workflow continua existindo e volta com uma
   troca de variável, sem editar código.
3. **No PC:** pare de subir o bot. O jeito reversível é a pausa
   (`python -c "from core import pausa; pausa.pausar('bot migrado para o servidor')"`);
   o definitivo é desabilitar as tarefas do Agendador criadas por
   `configurar_ciclo.ps1`.

Para voltar atrás, desfaça na ordem inversa.

---

## Dia a dia

```bash
bash deploy/botctl.sh status       # containers, papel, saúde
bash deploy/botctl.sh logs         # todos, ao vivo
bash deploy/botctl.sh logs rastreador
bash deploy/botctl.sh reiniciar    # relê o .env
bash deploy/botctl.sh atualizar    # puxa do GitHub e reconstrói
bash deploy/botctl.sh site         # regenera e publica o site
bash deploy/botctl.sh papel        # posso publicar agora? por quê?
bash deploy/botctl.sh parar        # derruba (volumes ficam)
```

Histórico das atualizações: `data/deploy.log`.
Erros estruturados: `docker exec bot_rastreador tail -20 /app/data/errors.jsonl`.

---

## Quando algo não vai bem

**"O servidor está quieto."**
Comece por `bash deploy/botctl.sh papel`. Em `nuvem`, ficar quieto das 08:30
às 02:35 é o comportamento correto, não um defeito.

**"O WhatsApp parou, o Telegram continua."**
É o desenho (Regra 6): o Telegram nunca depende do WhatsApp. Veja o estado da
sessão — se o celular ficou muito tempo sem internet, a Evolution desconecta
e é preciso ler o QR de novo:

```bash
bash deploy/botctl.sh qr
```

**"Nada publica."**
```bash
bash deploy/botctl.sh logs rastreador
```

**"Editei o `.env` e nada mudou."**
`bash deploy/botctl.sh reiniciar` — o `.env` é lido na subida do container.

**"Sem espaço em disco."**
```bash
docker system prune -af    # remove imagens antigas; volumes ficam
```
