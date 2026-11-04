"""Online SQLite backup utility (NFR-10).

Creates a consistent copy of the live database using SQLite's backup API,
which is safe while the Flask application is running. Old backups are
pruned automatically.

Usage:
    python backup_db.py [--keep 14]

Environment:
    DB_PATH      source database (default: ./maize_system.db)
    BACKUP_DIR   destination directory (default: ./backups)
"""

import argparse
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "maize_system.db"))
BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(BASE_DIR, "backups"))


def create_backup(keep=14):
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = os.path.join(BACKUP_DIR, f"maize_backup_{stamp}.db")

    source = sqlite3.connect(DB_PATH)
    destination = sqlite3.connect(target)
    try:
        with destination:
            source.backup(destination)
    finally:
        source.close()
        destination.close()

    backups = sorted(
        os.path.join(BACKUP_DIR, name)
        for name in os.listdir(BACKUP_DIR)
        if name.startswith("maize_backup_") and name.endswith(".db")
    )
    for old_path in backups[:-keep] if keep > 0 else []:
        try:
            os.remove(old_path)
        except OSError:
            pass

    print(f"[SUCCESS] Backup written to {target}")
    return target


def main():
    parser = argparse.ArgumentParser(description="Back up the maize system database.")
    parser.add_argument("--keep", type=int, default=14,
                        help="number of most recent backups to retain (default: 14)")
    args = parser.parse_args()
    create_backup(keep=args.keep)


if __name__ == "__main__":
    main()
