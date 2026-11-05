#!/bin/sh
set -e

mkdir -p /app/data /app/instance/uploads /app/backups

if [ ! -f "$DB_PATH" ]; then
    echo "[+] Database not found at $DB_PATH - initialising..."
    python init_db.py
else
    echo "[+] Applying schema migrations..."
    python init_db.py --migrate-only 2>/dev/null || python init_db.py
fi

# Daily online backup with automatic pruning (NFR-10). Retry with jitter so a
# restart loop cannot create a thundering herd of backups.
if [ "${ENABLE_DAILY_BACKUP:-1}" = "1" ]; then
    (
        sleep "${BACKUP_INITIAL_DELAY:-300}"
        while true; do
            python backup_db.py >> /app/backups/backup.log 2>&1 || true
            sleep 86400
        done
    ) &
fi

# Periodically purge evidence images past the retention window (SEC-09).
if [ "${ENABLE_IMAGE_PURGE:-1}" = "1" ]; then
    (
        while true; do
            python manage.py purge-images >> /app/backups/purge.log 2>&1 || true
            sleep 86400
        done
    ) &
fi

exec "$@"
