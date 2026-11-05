# Maize Disease Diagnostic System - production image
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/maize_system.db \
    UPLOAD_FOLDER=/app/instance/uploads \
    BACKUP_DIR=/app/backups \
    APP_CONFIG=production \
    SESSION_COOKIE_SECURE=1 \
    TRUSTED_PROXY_HOPS=1 \
    PORT=5000

WORKDIR /app

# System libraries required by TensorFlow / Pillow.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code, configuration, templates and static assets.
COPY msds/ ./msds/
COPY app.py i18n.py init_db.py backup_db.py maize_schema.sql wsgi.py ./
COPY docker-entrypoint.sh ./
COPY templates/ ./templates/
COPY static/ ./static/
COPY models/ ./models/

# Non-root runtime user (SEC-10).
RUN chmod +x /app/docker-entrypoint.sh \
    && mkdir -p /app/data /app/instance/uploads /app/backups \
    && useradd --create-home --uid 10001 maize \
    && chown -R maize:maize /app/data /app/instance /app/backups /app

USER maize

# Persistent database, retained leaf images and backups.
VOLUME ["/app/data", "/app/instance", "/app/backups"]

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
# Single worker keeps the model in one process; scale horizontally behind nginx.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", \
     "--timeout", "120", "wsgi:app"]
