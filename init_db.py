import sqlite3
import os
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "maize_system.db"))
SCHEMA_PATH = os.environ.get(
    "SCHEMA_PATH", os.path.join(BASE_DIR, "maize_schema.sql")
)

def init_database():
    print(f"[+] Initializing relational SQLite database (WAL mode): {DB_PATH}")
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    # Read schema script
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Enable foreign keys and WAL mode
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("PRAGMA journal_mode = WAL;")

    # Execute schema setup (tables, triggers, indexes, seeds)
    cursor.executescript(schema_sql)

    # Seed Village Sections (Liwaladzi Area)
    sections = [
        ("Nzumala", "Liwaladzi"),
        ("Gowoka", "Liwaladzi"),
        ("Kambwiri", "Liwaladzi"),
        ("Zunga", "Liwaladzi"),
        ("Nsindwa", "Liwaladzi"),
        ("Kautaka", "Liwaladzi"),
        ("Liwaladzi Central", "Liwaladzi")
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO village_section (section_name, epa_name) VALUES (?, ?);", 
        sections
    )

    # Seed Bilingual IPM Advisories (FR-10, FR-22)
    advisories = [
        # Downy Mildew (Class ID 0)
        (0, 'en', 'DISEASE DETECTED: Downy Mildew', 
         'Fungal-like infection causing yellow leaves and white downy growth.',
         'Apply metalaxyl-based seed treatment. Uproot and burn severely infected plants.', 
         'Malawi Ministry of Agriculture IPM Guidelines (2025)'),
        (0, 'ny', 'MATENDA APAPEZEKA: Downy Mildew (Chuku)', 
         'Matenda a bowa omwe amapangitsa masamba kukhala achikasu ndi zoyera kunsi kwa tsamba.',
         'Gwiritsani ntchito mankhwala a metalaxyl pothira mbeu. Zula ndi kuotcha mbewu zomwe zakhudzidwa kwambiri.', 
         'Zotsatira za Unduna wa za Ulimi mu Malawi (2025)'),

        # Healthy (Class ID 1)
        (1, 'en', 'HEALTHY CROP', 
         'No symptoms of MSV, MLN, or Downy Mildew detected.',
         'Maintain good field hygiene, crop rotation, and balanced fertilization.', 
         'Standard Agronomic Best Practices'),
        (1, 'ny', 'MBEWU YATHANZI', 
         'Palibe zizindikiro za matenda a MSV, MLN, kapena Downy Mildew zomwe zapezeka.',
         'Sungani ulimi waukhondo, sinthanitsani mbewu pamunda, ndipo thirirani feteleza wokwanira.', 
         'Zotsatira za Ulimi Wambewu'),

        # MLN (Class ID 2)
        (2, 'en', 'DISEASE DETECTED: Maize Lethal Necrosis (MLN)', 
         'Co-infection of MCMV and a potyvirus causing yellowing and leaf necrosis.',
         'Uproot infected plants immediately. Control thrips/leaf beetles. Rotate with non-cereal crops.', 
         'Sub-Saharan Africa MLN Management Protocol'),
        (2, 'ny', 'MATENDA APAPEZEKA: Maize Lethal Necrosis (MLN)', 
         'Matenda a chiwiri omwe amapangitsa masamba kuwama ndi kuuma kuchokera mumphepete.',
         'Zula mbewu zodwala nsonga nthawi yomweyo. Phesani tizilombo. Sinthanitsani mbewu ndi zomwe si mbeu zambewu.', 
         'Ndondomeko ya Ulimi wa MLN mu Africa'),

        # MSV (Class ID 3)
        (3, 'en', 'DISEASE DETECTED: Maize Streak Virus (MSV)', 
         'Viral disease spread by leafhoppers causing continuous chlorotic streaks.',
         'Plant certified virus-tolerant varieties. Manage leafhopper populations early using eco-friendly biopesticides.', 
         'Shepherd et al. (2010) / Malawi National IPM Advisory'),
        (3, 'ny', 'MATENDA APAPEZEKA: Maize Streak Virus (MSV / Kawalala)', 
         'Matenda a vaillasi ofalitsidwa ndi nsikidzi zomwe zimapangitsa mizere yachikasu pamasamba.',
         'Dzalani mbewu zolimbana ndi kawalala. Phesani nsikidzi msanga pogwiritsa ntchito mankhwala ovomerezeka.', 
         'Malawi National IPM Advisory')
    ]
    cursor.executemany(
        """INSERT OR REPLACE INTO advisory 
           (class_id, language_code, status_text, description, action_text, source_ref) 
           VALUES (?, ?, ?, ?, ?, ?);""",
        advisories
    )

    # Seed First Administrator Account (FR-18)
    admin_user = "rexious_admin"
    admin_email = "bit22-rhuka@mubas.ac.mw"
    admin_pass = generate_password_hash("AdminPass123!", method="pbkdf2:sha256")
    
    cursor.execute("""
        INSERT OR IGNORE INTO app_user (full_name, username, email, password_hash, role_id)
        VALUES ('Rexious Huka', ?, ?, ?, 3);
    """, (admin_user, admin_email, admin_pass))

    conn.commit()
    conn.close()
    print("[SUCCESS] Database initialized successfully with tables, triggers, advisories, and Admin account.")

if __name__ == "__main__":
    init_database()