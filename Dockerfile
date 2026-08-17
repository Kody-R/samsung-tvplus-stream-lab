FROM python:3.12-slim
ARG VERSION=0.2.1
LABEL org.opencontainers.image.title="Samsung TV Plus Stream Lab" \
      org.opencontainers.image.description="Isolated IPTV HLS/FFmpeg troubleshooting laboratory" \
      org.opencontainers.image.version="${VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/Chicago
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates tzdata procps iproute2 lsof && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /app/data/sessions
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD curl -fsS http://127.0.0.1:8091/health || exit 1
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8091","--workers","1"]
