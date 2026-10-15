FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000 \
    REPORT_BOT_DATA_DIR=/app/data \
    REPORT_BOT_JOB_DIR=/app/data/jobs \
    REPORT_BOT_SETTINGS_PATH=/app/data/settings.json

RUN apt-get update && apt-get install --no-install-recommends -y \
    curl \
    fontconfig \
    fonts-liberation \
    libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY ["verification page template.docx", "./verification page template.docx"]
COPY ["verification page template.dotx", "./verification page template.dotx"]

RUN useradd -m appuser \
    && mkdir -p /app/data/jobs \
    && chown -R appuser:appuser /app /home/appuser

ENV HOME=/home/appuser
USER appuser
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
