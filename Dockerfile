FROM python:3.12-slim

LABEL org.opencontainers.image.title="DockVault" \
      org.opencontainers.image.description="Docker-Backup und Wiederherstellung fuer Unraid" \
      org.opencontainers.image.source="https://github.com/gottschalkfelix4-source/dockvault"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DV_CONFIG_DIR=/config \
    DV_BACKUP_DIR=/backups \
    DV_PORT=8080 \
    TZ=Europe/Berlin

WORKDIR /app

# tar/zstd fuer manuelle Eingriffe, tzdata fuer Zeitplaene,
# cifs-utils fuer SMB-Backup-Ziele (benoetigt zusaetzlich --cap-add SYS_ADMIN)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata tar zstd curl cifs-utils \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY web/ ./web/

VOLUME ["/config", "/backups"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${DV_PORT}/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
