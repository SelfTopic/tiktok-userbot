# 3.11: у tgcrypto есть готовые колёса только до 3.11, компилятор не нужен
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# venv принадлежит пользователю app, чтобы бот мог сам обновлять yt-dlp и gallery-dl
RUN useradd --create-home --uid 1000 app \
    && python -m venv /opt/venv \
    && mkdir -p /app/data \
    && chown -R app:app /opt/venv /app
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data

USER app
WORKDIR /app
COPY --chown=app:app requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app bot.py downloader.py ./
# Проверка, что зависимости ставятся и импортируются
RUN python -c "import pyrogram, tgcrypto, qrcode, yt_dlp, gallery_dl, httpx, dotenv" \
    && ffmpeg -version > /dev/null
VOLUME /app/data

CMD ["python", "bot.py"]
