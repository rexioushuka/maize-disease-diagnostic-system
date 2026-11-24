import json
import os
import platform
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from tests.test_db_safeguards import CHECKS

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "db_safeguard_checks.json")


def main():
    results = []
    for check_id, description, check in CHECKS:
        with tempfile.TemporaryDirectory() as work:
            db_path = os.path.join(work, "safeguards.db")
            db = sqlite3.connect(db_path)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON;")
            with open(os.path.join(BASE, "maize_schema.sql"), encoding="utf-8") as handle:
                db.executescript(handle.read())
            db.execute(
                "INSERT INTO village_section (section_name, epa_name) "
                "VALUES ('Nzumala', 'Liwaladzi');"
            )
            db.commit()
            expected, actual, status = check(db)
            db.close()
        results.append({
            "id": check_id,
            "check": description,
            "expected": expected,
            "actual": actual,
            "status": status,
        })

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema": "maize_schema.sql",
        "method": "pytest tests/test_db_safeguards.py -q; PRAGMA foreign_keys=ON",
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "working_directory": os.getcwd(),
        "summary": {
            "total": len(results),
            "passed": sum(item["status"] == "Pass" for item in results),
            "failed": sum(item["status"] != "Pass" for item in results),
        },
        "checks": results,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    print(f"{payload['summary']['passed']}/{payload['summary']['total']} DB checks passed")
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
