"""Create / migrate the SQLite database.

The first administrator is created from MAIZE_ADMIN_USERNAME / MAIZE_ADMIN_EMAIL
and MAIZE_ADMIN_PASSWORD (or a securely generated password that must be changed
on first login). No credential is hard-coded (SEC-01).
"""

import os
import secrets
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "maize_system.db"))
SCHEMA_PATH = os.environ.get(
    "SCHEMA_PATH", os.path.join(BASE_DIR, "maize_schema.sql")
)

# Columns that were added after the first release; applied to legacy databases.
MIGRATIONS = [
    ("system_log", "prev_hash", "TEXT"),
    ("system_log", "row_hash", "TEXT"),
    # SQLite forbids ADD COLUMN with REFERENCES and a non-NULL default,
    # so the migrated column carries the default but no FK clause.
    ("officer_invitation", "role_id", "INTEGER NOT NULL DEFAULT 2"),
]


def _table_exists(cursor, name):
    return cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;", (name,)
    ).fetchone() is not None


def _column_exists(cursor, table, column):
    return any(r[1] == column for r in cursor.execute(f"PRAGMA table_info({table});"))


def apply_migrations(cursor):
    for table, column, ddl in MIGRATIONS:
        if _table_exists(cursor, table) and not _column_exists(cursor, table, column):
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl};")
            print(f"[+] Migrated: added {table}.{column}")
    # New tables for existing databases.
    if not _table_exists(cursor, "officer_section"):
        cursor.execute(
            "CREATE TABLE officer_section ("
            "user_id INTEGER NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,"
            "section_id INTEGER NOT NULL REFERENCES village_section(section_id) "
            "ON DELETE CASCADE, PRIMARY KEY (user_id, section_id));"
        )
        print("[+] Migrated: created officer_section")
    if not _table_exists(cursor, "registration_verification"):
        cursor.execute(
            "CREATE TABLE registration_verification ("
            "verification_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "public_id TEXT NOT NULL UNIQUE,"
            "contact TEXT NOT NULL,"
            "contact_kind TEXT NOT NULL CHECK (contact_kind IN ('email','phone')),"
            "full_name TEXT NOT NULL,"
            "username TEXT NOT NULL,"
            "section_id INTEGER NOT NULL REFERENCES village_section(section_id),"
            "password_hash TEXT NOT NULL,"
            "code_hash TEXT NOT NULL,"
            "attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),"
            "expires_at TEXT NOT NULL,"
            "created_at TEXT NOT NULL DEFAULT "
            "(strftime('%Y-%m-%dT%H:%M:%fZ','now')));"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_regver_contact "
            "ON registration_verification(contact);"
        )
        print("[+] Migrated: created registration_verification")
    if not _table_exists(cursor, "password_reset_code"):
        cursor.execute(
            "CREATE TABLE password_reset_code ("
            "code_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "public_id TEXT NOT NULL UNIQUE,"
            "user_id INTEGER NOT NULL REFERENCES app_user(user_id) "
            "ON DELETE CASCADE,"
            "code_hash TEXT NOT NULL,"
            "attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),"
            "expires_at TEXT NOT NULL,"
            "created_at TEXT NOT NULL DEFAULT "
            "(strftime('%Y-%m-%dT%H:%M:%fZ','now')));"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_resetcode_user "
            "ON password_reset_code(user_id);"
        )
        print("[+] Migrated: created password_reset_code")
    if not _table_exists(cursor, "password_reset_token"):
        cursor.execute(
            "CREATE TABLE password_reset_token ("
            "token_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "user_id INTEGER NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,"
            "token_hash TEXT NOT NULL UNIQUE,"
            "created_at TEXT NOT NULL DEFAULT "
            "(strftime('%Y-%m-%dT%H:%M:%fZ','now')),"
            "expires_at TEXT NOT NULL, used_at TEXT);"
        )
        print("[+] Migrated: created password_reset_token")


def seed_admin(cursor):
    from werkzeug.security import generate_password_hash

    username = os.environ.get("MAIZE_ADMIN_USERNAME", "admin")
    email = os.environ.get("MAIZE_ADMIN_EMAIL")
    password = os.environ.get("MAIZE_ADMIN_PASSWORD")

    existing = cursor.execute(
        "SELECT COUNT(*) FROM app_user u JOIN role r ON u.role_id=r.role_id "
        "WHERE r.role_name='ADMIN';"
    ).fetchone()[0]
    if existing:
        print("[=] An administrator already exists; not seeding another.")
        return

    generated = False
    if not password:
        password = "Mb-" + secrets.token_urlsafe(18)
        generated = True
    if not email:
        email = f"{username}@localhost"

    cursor.execute(
        """
        INSERT INTO app_user
            (full_name, username, email, password_hash, role_id,
             must_change_password)
        VALUES (?, ?, ?, ?, 3, 1);
        """,
        ("System Administrator", username, email,
         generate_password_hash(password, method="pbkdf2:sha256")),
    )
    if generated:
        print(f"[!] Generated admin password (must change on first login): {password}")
        print("    Set MAIZE_ADMIN_PASSWORD to control this value.")


def init_database():
    print(f"[+] Initializing SQLite database: {DB_PATH}")
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        schema_sql = fh.read()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.executescript(schema_sql)
    apply_migrations(cursor)

    sections = [
        ("Nzumala", "Liwaladzi"), ("Gowoka", "Liwaladzi"),
        ("Kambwiri", "Liwaladzi"), ("Zunga", "Liwaladzi"),
        ("Nsindwa", "Liwaladzi"), ("Kautaka", "Liwaladzi"),
        ("Liwaladzi Central", "Liwaladzi"),
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO village_section (section_name, epa_name) VALUES (?, ?);",
        sections,
    )

    advisories = _advisories()
    cursor.executemany(
        """INSERT OR REPLACE INTO advisory
           (class_id, language_code, status_text, description, action_text, source_ref)
           VALUES (?, ?, ?, ?, ?, ?);""",
        advisories,
    )

    seed_admin(cursor)
    conn.commit()
    conn.close()
    print("[SUCCESS] Database ready.")


def _advisories():
    return [
        (0, 'en', 'DISEASE DETECTED: Downy Mildew',
         'Fungal-like infection causing yellow leaves and white downy growth.',
         'Apply metalaxyl-based seed treatment. Uproot and burn severely infected plants.',
         'Malawi Ministry of Agriculture IPM Guidelines (2025)'),
        (0, 'ny', 'MATENDA APAPEZEKA: Downy Mildew (Chuku)',
         'Matenda a bowa omwe amapangitsa masamba kukhala achikasu ndi zoyera kunsi kwa tsamba.',
         'Gwiritsani ntchito mankhwala a metalaxyl pothira mbeu. Zula ndi kuotcha mbewu zomwe zakhudzidwa kwambiri.',
         'Zotsatira za Unduna wa za Ulimi mu Malawi (2025)'),
        (1, 'en', 'HEALTHY CROP',
         'No symptoms of MSV, MLN, or Downy Mildew detected.',
         'Maintain good field hygiene, crop rotation, and balanced fertilization.',
         'Standard Agronomic Best Practices'),
        (1, 'ny', 'MBEWU YATHANZI',
         'Palibe zizindikiro za matenda a MSV, MLN, kapena Downy Mildew zomwe zapezeka.',
         'Sungani ulimi waukhondo, sinthanitsani mbewu pamunda, ndipo thirirani feteleza wokwanira.',
         'Zotsatira za Ulimi Wambewu'),
        (2, 'en', 'DISEASE DETECTED: Maize Lethal Necrosis (MLN)',
         'Co-infection of MCMV and a potyvirus causing yellowing and leaf necrosis.',
         'Uproot infected plants immediately. Control thrips/leaf beetles. Rotate with non-cereal crops.',
         'Sub-Saharan Africa MLN Management Protocol'),
        (2, 'ny', 'MATENDA APAPEZEKA: Maize Lethal Necrosis (MLN)',
         'Matenda a chiwiri omwe amapangitsa masamba kuwama ndi kuuma kuchokera mumphepete.',
         'Zula mbewu zodwala nsonga nthawi yomweyo. Phesani tizilombo. Sinthanitsani mbewu ndi zomwe si mbeu zambewu.',
         'Ndondomeko ya Ulimi wa MLN mu Africa'),
        (3, 'en', 'DISEASE DETECTED: Maize Streak Virus (MSV)',
         'Viral disease spread by leafhoppers causing continuous chlorotic streaks.',
         'Plant certified virus-tolerant varieties. Manage leafhopper populations early using eco-friendly biopesticides.',
         'Shepherd et al. (2010) / Malawi National IPM Advisory'),
        (3, 'ny', 'MATENDA APAPEZEKA: Maize Streak Virus (MSV / Kawalala)',
         'Matenda a vaillasi ofalitsidwa ndi nsikidzi zomwe zimapangitsa mizere yachikasu pamasamba.',
         'Dzalani mbewu zolimbana ndi kawalala. Phesani nsikidzi msanga pogwiritsa ntchito mankhwala ovomerezeka.',
         'Malawi National IPM Advisory'),
    ]


if __name__ == "__main__":
    if "--migrate-only" in sys.argv and os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON;")
        cur.execute("PRAGMA journal_mode = WAL;")
        apply_migrations(cur)
        conn.commit()
        conn.close()
        print("[SUCCESS] Migrations applied.")
    else:
        init_database()
