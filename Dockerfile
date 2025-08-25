FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

# System + browser deps in one layer
RUN apt-get update && apt-get install -y --no-install-recommends \
    # your tools
    postgresql-client gcc \
    # Playwright/Chromium deps (Debian 12/bookworm names)
    ca-certificates wget gnupg \
    libasound2 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 \
    libcairo2 libcups2 libdbus-1-3 libdrm2 libexpat1 \
    libgbm1 libglib2.0-0 libgtk-3-0 libnspr4 libnss3 \
    libpango-1.0-0 libx11-6 libx11-xcb1 libxcb1 libxcb-dri3-0 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
    libxkbcommon0 libxshmfence1 libgdk-pixbuf-2.0-0 \
    libjpeg62-turbo libwebp7 libvpx7 libenchant-2-2 \
    fonts-unifont fonts-liberation fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# venv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# deps
COPY requirements.txt .
RUN pip install -r requirements.txt

# Install the browser binaries (no install-deps!)
RUN playwright install chromium

# app
COPY . .

# simple runner
RUN printf '%s\n' '#!/bin/bash' \
  'python -u core.py 2>&1 | tee /app/app.log' \
  'tail -f /app/app.log' > /app/run.sh && chmod +x /app/run.sh

CMD ["/app/run.sh"]
