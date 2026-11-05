"""Registration contact verification via Brevo (mocked, no network)."""

import hashlib
import os
import sqlite3

import pytest
from conftest import csrf

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(BASE, "maize_schema.sql")

FORM = {
    "full_name": "Test Farmer",
    "username": "farmerx",
    "contact": "farmerx@example.com",
    "password": "ValidPassword123!",
    "confirm_password": "ValidPassword123!",
    "section_id": "1",
}


@pytest.fixture()
def brevo_ctx(tmp_path, monkeypatch):
    db_path = str(tmp_path / "verify.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(open(SCHEMA, encoding="utf-8").read())
    conn.executemany(
        "INSERT INTO village_section (section_name, epa_name) VALUES (?, ?);",
        [("Nzumala", "Liwaladzi"), ("Gowoka", "Liwaladzi")],
    )
    conn.commit()
    conn.close()

    from msds import create_app

    application = create_app("testing", overrides={
        "DB_PATH": db_path,
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "LOAD_MODEL": False,
        "SECRET_KEY": "test-secret",
        "AUDIT_HMAC_KEY": "audit-secret",
        "TESTING": True,
        "BREVO_API_KEY": "test-key",
        "BREVO_SENDER_EMAIL": "sender@example.com",
    })
    os.makedirs(str(tmp_path / "uploads"), exist_ok=True)

    sent = []

    def fake_send(contact_kind, contact, code):
        sent.append({"kind": contact_kind, "contact": contact, "code": code})

    monkeypatch.setattr("msds.services.brevo.send_verification_code", fake_send)
    return application.test_client(), db_path, sent


def _user_count(db_path, username):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM app_user WHERE username = ?;", (username,)
        ).fetchone()[0]
    finally:
        conn.close()


def _register(client):
    token = csrf(client, "/register")
    return client.post("/register", data={**FORM, "_csrf_token": token},
                       follow_redirects=False)


def test_register_defers_account_until_code_verified(brevo_ctx):
    client, db_path, sent = brevo_ctx
    resp = _register(client)
    assert resp.status_code == 302
    assert "/register/verify/" in resp.headers["Location"]
    assert len(sent) == 1
    assert sent[0]["kind"] == "email"
    assert _user_count(db_path, "farmerx") == 0


def test_correct_code_creates_account(brevo_ctx):
    client, db_path, sent = brevo_ctx
    location = _register(client).headers["Location"]
    token = csrf(client, location)
    resp = client.post(location, data={"code": sent[0]["code"],
                                       "_csrf_token": token},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")
    assert _user_count(db_path, "farmerx") == 1


def test_wrong_code_rejected_and_not_created(brevo_ctx):
    client, db_path, sent = brevo_ctx
    location = _register(client).headers["Location"]
    wrong = "000000" if sent[0]["code"] != "000000" else "111111"
    token = csrf(client, location)
    resp = client.post(location, data={"code": wrong, "_csrf_token": token},
                       follow_redirects=True)
    assert _user_count(db_path, "farmerx") == 0
    assert "invalid" in resp.get_data(as_text=True).lower()


def test_expired_code_rejected(brevo_ctx):
    client, db_path, sent = brevo_ctx
    location = _register(client).headers["Location"]
    token = csrf(client, location)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE registration_verification SET expires_at = ?;",
                 ("2000-01-01T00:00:00Z",))
    conn.commit()
    conn.close()
    resp = client.post(location, data={"code": sent[0]["code"],
                                       "_csrf_token": token},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/register")
    assert _user_count(db_path, "farmerx") == 0


def test_stored_code_is_hashed(brevo_ctx):
    client, db_path, sent = brevo_ctx
    _register(client)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT code_hash FROM registration_verification;").fetchone()
    conn.close()
    assert row[0] == hashlib.sha256(sent[0]["code"].encode()).hexdigest()
    assert sent[0]["code"] not in row[0]
