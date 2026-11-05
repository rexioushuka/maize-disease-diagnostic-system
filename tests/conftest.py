"""Pytest fixtures: isolated DB + stub model per test session."""

import os
import sqlite3
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

os.environ.setdefault("LOAD_MODEL", "0")
# Keep tests hermetic: never pick up real Brevo credentials from instance/brevo.env.
for _var in ("BREVO_API_KEY", "BREVO_SENDER_EMAIL", "BREVO_SENDER_NAME",
             "BREVO_SMS_SENDER", "BREVO_SMTP_LOGIN", "BREVO_SMTP_KEY"):
    os.environ[_var] = ""


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    schema = os.path.join(BASE, "maize_schema.sql")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(open(schema, encoding="utf-8").read())
    # Minimal reference data for tests.
    conn.executemany(
        "INSERT INTO village_section (section_name, epa_name) VALUES (?, ?);",
        [("Nzumala", "Liwaladzi"), ("Gowoka", "Liwaladzi")],
    )
    conn.commit()
    conn.close()

    from werkzeug.security import generate_password_hash

    from msds import create_app
    from msds.db import connect

    application = create_app("testing", overrides={
        "DB_PATH": db_path,
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "LOAD_MODEL": False,
        "CLASS_MAPPING_PATH": os.path.join(BASE, "models", "class_mapping.json"),
        "SECRET_KEY": "test-secret",
        "AUDIT_HMAC_KEY": "audit-secret",
        "TESTING": True,
    })
    os.makedirs(str(tmp_path / "uploads"), exist_ok=True)

    db = connect(db_path)
    pwd = generate_password_hash("CorrectHorse123!", method="pbkdf2:sha256",
                                 salt_length=8)
    admin_id = db.execute(
        "INSERT INTO app_user (full_name, username, email, password_hash, "
        "role_id) VALUES ('Admin', 'admin', 'admin@example.com', ?, 3);",
        (pwd,),
    ).lastrowid
    db.execute(
        "INSERT INTO app_user (full_name, username, email, password_hash, "
        "role_id, section_id) VALUES ('Farmer', 'farmer', 'f@example.com', ?, 1, 1);",
        (pwd,),
    )
    officer_id = db.execute(
        "INSERT INTO app_user (full_name, username, email, password_hash, "
        "role_id, created_by) VALUES ('Officer', 'officer', 'o@example.com', ?, 2, ?);",
        (pwd, admin_id),
    ).lastrowid
    db.execute(
        "INSERT INTO officer_section (user_id, section_id) VALUES (?, 1);",
        (officer_id,),
    )
    db.commit()
    db.close()

    from msds.extensions import rate_limiter
    rate_limiter.reset()

    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client, path="/login"):
    """Return a CSRF token from a rendered page."""
    import re

    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="_csrf_token"\s+value="([^"]+)"', html)
    if not m:
        m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    return m.group(1) if m else None


@pytest.fixture()
def login(client):
    def _login(username="farmer", password="CorrectHorse123!"):
        token = csrf(client, "/login")
        return client.post(
            "/login",
            data={"login_input": username, "password": password,
                  "_csrf_token": token},
            follow_redirects=False,
        )
    return _login
