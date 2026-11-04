#!/bin/sh
set -e

mkdir -p /app/data /app/instance/uploads /app/backups

if [ ! -f "$DB_PATH" ]; then
    echo "[+] Database not found at $DB_PATH - initialising..."
    python init_db.py
fi

# Daily online backup with automatic pruning (NFR-10).
if [ "${ENABLE_DAILY_BACKUP:-1}" = "1" ]; then
    (
        while true; do
            python backup_db.py >> /app/backups/backup.log 2>&1 || true
            sleep 86400
        done
    ) &
fi

exec "$@"
