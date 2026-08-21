FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg for media; Node 22+ required by yt-dlp EJS challenge solving.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY app ./app
COPY static ./static
COPY downloader ./downloader
COPY README.md ./README.md

# Keep downloader fallback enabled for environments without .NET artifacts.
ENV DOWNLOADER_FALLBACK_TO_YTDLP=1 \
    ATTACH_DOWNLOAD_AUDIO_ONLY=1 \
    YTDLP_JS_RUNTIMES=node \
    YTDLP_REMOTE_COMPONENTS=ejs:github

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
