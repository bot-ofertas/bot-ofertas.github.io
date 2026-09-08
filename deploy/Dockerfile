# Imagem dos 4 processos do bot num servidor Linux (DigitalOcean e afins).
#
# Duas escolhas que parecem detalhe e não são:
#
# 1. A tag do Python é FIXADA em `-bookworm`. `python:3.13-slim` sozinho
#    segue o Debian estável do dia; quando o Debian virou `trixie`, meia
#    dúzia de bibliotecas do Chromium foi renomeada (`libasound2` ->
#    `libasound2t64`, `libatk1.0-0` -> `libatk1.0-0t64`). Uma lista de
#    pacotes escrita à mão quebra num `docker build` que ontem funcionava,
#    sem ninguém ter mexido em nada. Com a tag fixa, o dia em que o build
#    mudar de distro é o dia em que alguém escolheu isso.
#
# 2. Quem instala as bibliotecas do Chromium é o próprio Playwright
#    (`install --with-deps`), não uma lista mantida aqui. Ele sabe o nome
#    certo dos pacotes em cada distro e atualiza junto com a versão do
#    navegador — é uma lista a menos para envelhecer em silêncio.
FROM python:3.13-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

# tzdata é obrigatório, não conforto: o ciclo diário (core/janela.py) compara
# com o relógio LOCAL. Sem tzdata, `TZ=America/Sao_Paulo` é ignorado, o
# container fica em UTC e a janela 08:30–02:00 escorrega 3 horas — o papel
# `nuvem` se acharia livre das 23:00 às 05:30 BRT e publicaria por cima do PC
# ligado, sem erro nenhum no log. Ver `core/papel._fuso_conferido()`.
#
# `git` tambem nao e conforto: `core/papel.py` decide se o PC local esta mesmo
# publicando lendo as marcas que o `core/site_publisher.py` deixa no historico
# de `docs/`. Sem o binario, aquela checagem devolve "nao consegui saber" para
# sempre e o servidor fica calado o dia inteiro esperando um PC que pode estar
# fora do ar ha semanas — em silencio, que e o pior jeito de falhar.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget tzdata git \
 && rm -rf /var/lib/apt/lists/*

# Só as libs que rodam em Linux — sem pyautogui/pywinauto/pywin32, que são a
# automação do WhatsApp Desktop do Windows e não existem aqui. No servidor o
# WhatsApp sai pela Evolution API (integrations/whatsapp_api.py).
COPY deploy/requirements.linux.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium

COPY . /app

RUN mkdir -p /app/data /app/assets

# O `.git` chega por montagem do servidor (ver docker-compose.vps.yml), com
# dono podendo nao bater com o usuario do container. Nesse caso o git recusa
# ler o repositorio ("detected dubious ownership") e a prova de vida do PC
# volta a responder "nao sei" — de novo em silencio. `safe.directory` custa
# uma linha e fecha esse caminho.
RUN git config --global --add safe.directory /app

EXPOSE 8724

CMD ["python", "-u", "deploy/entrypoint.py"]
