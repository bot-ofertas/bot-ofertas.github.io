FROM python:3.13-slim

WORKDIR /app

# Playwright + Chromium dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget gnupg \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libatspi2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# Requirements (só as libs que rodam em Linux — remove pyautogui/pywinauto/pywin32)
COPY deploy/requirements.linux.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium

# Copia código
COPY . /app

# Cria pasta data
RUN mkdir -p /app/data

# Healthcheck endpoint em :8724
EXPOSE 8724

CMD ["python", "-u", "deploy/entrypoint.py", "--random", "--loop-min", "30", "--loop-max", "45"]
