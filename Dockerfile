# Build SoundMind.Downloader (YoutubeExplode) for Linux so Render uses it
# instead of falling back to yt-dlp when the Windows .exe is missing.
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS downloader-build
WORKDIR /src
COPY downloader/Directory.Build.props \
     downloader/Directory.Packages.props \
     downloader/global.json \
     downloader/NuGet.config \
     ./
COPY downloader/YoutubeDownloader.Core ./YoutubeDownloader.Core
COPY downloader/SoundMind.Downloader ./SoundMind.Downloader
RUN dotnet publish SoundMind.Downloader/SoundMind.Downloader.csproj \
    -c Release \
    -r linux-x64 \
    --self-contained true \
    -p:PublishSingleFile=true \
    -p:DebugType=None \
    -o /publish \
 && chmod +x /publish/SoundMind.Downloader

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg for media; Node 22+ for optional yt-dlp EJS fallback.
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
COPY --from=downloader-build /publish /app/tools/soundmind

ENV DOWNLOADER_FALLBACK_TO_YTDLP=1 \
    ATTACH_DOWNLOAD_AUDIO_ONLY=1 \
    YTDLP_JS_RUNTIMES=node \
    YTDLP_REMOTE_COMPONENTS=ejs:github \
    SOUNDMIND_DOWNLOADER_PATH=/app/tools/soundmind/SoundMind.Downloader \
    PATH="/app/tools/soundmind:${PATH}"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
