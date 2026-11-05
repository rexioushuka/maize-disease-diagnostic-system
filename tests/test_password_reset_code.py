"""Password reset via Brevo-delivered 6-digit code (mocked, no network)."""

import os
import sqlite3

import pytest
from conftest import csrf

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(BASE, "maize_schema.sql")


@pytest.fixture()
def brevo_ctx(tmp_path, monkeypatch):
    db_path = str(tmp_path / "reset.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(open(SCHEMA, encoding="utf-8").read())
    conn.executemany(
        "INSERT INTO village_section (section_name, epa_name) VALUES (?, ?);",
        [("Nzumala", "Liwaladzi")],
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
        "SECRET_KEY": "test-secret",
        "AUDIT_HMAC_KEY": "audit-secret",
        "TESTING": True,
        "BREVO_API_KEY": "test-key",
        "BREVO_SENDER_EMAIL": "sender@example.com",
    })
    os.makedirs(str(tmp_path / "uploads"), exist_ok=True)

    db = connect(db_path)
    pwd = generate_password_hash("OldPassword123!", method="pbkdf2:sha256",
                                 salt_length=8)
    db.execute(
        "INSERT INTO app_user (full_name, username, email, password_hash, "
        "role_id) VALUES ('Admin', 'admin', 'admin@example.com', ?, 3);",
        (pwd,),
    )
    db.execute(
        "INSERT INTO app_user (full_name, username, email, password_hash, "
        "role_id, section_id) VALUES ('Farmer', 'farmer', 'farmer@example.com', ?, 1, 1);",
        (pwd,),
    )
    db.commit()
    db.close()

    sent = []

    def fake_send(contact_kind, contact, code):
        sent.append({"kind": contact_kind, "contact": contact, "code": code})

    monkeypatch.setattr("msds.services.brevo.send_verification_code", fake_send)
    from msds.extensions import rate_limiter
    rate_limiter.reset()
    return application.test_client(), db_path, sent


def _request_code(client, identifier="farmer"):
    token = csrf(client, "/forgot-password")
    return client.post("/forgot-password",
                       data={"identifier": identifier, "_csrf_token": token},
                       follow_redirects=False)


def _pending_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM password_reset_code;").fetchone()[0]
    finally:
        conn.close()


def test_known_identifier_sends_code(brevo_ctx):
    client, db_path, sent = brevo_ctx
    resp = _request_code(client, "farmer")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/forgot-password/verify")
    assert len(sent) == 1
    assert sent[0]["kind"] == "email"
    assert sent[0]["contact"] == "farmer@example.com"
    assert _pending_count(db_path) == 1


def test_unknown_identifier_is_indistinguishable(brevo_ctx):
    client, db_path, sent = brevo_ctx
    resp = _request_code(client, "ghost")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/forgot-password/verify")
    assert sent == []
    assert _pending_count(db_path) == 0


def test_correct_code_redirects_to_reset_form(brevo_ctx):
    client, db_path, sent = brevo_ctx
    _request_code(client)
    token = csrf(client, "/forgot-password/verify")
    resp = client.post("/forgot-password/verify",
                       data={"code": sent[0]["code"], "_csrf_token": token},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert "/reset-password/" in resp.headers["Location"]
    assert client.get(resp.headers["Location"]).status_code == 200


def test_wrong_code_rejected(brevo_ctx):
    client, db_path, sent = brevo_ctx
    _request_code(client)
    wrong = "000000" if sent[0]["code"] != "000000" else "111111"
    token = csrf(client, "/forgot-password/verify")
    resp = client.post("/forgot-password/verify",
                       data={"code": wrong, "_csrf_token": token},
                       follow_redirects=True)
    assert "invalid" in resp.get_data(as_text=True).lower()
    assert _pending_count(db_path) == 1


def test_expired_code_rejected(brevo_ctx):
    client, db_path, sent = brevo_ctx
    _request_code(client)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE password_reset_code SET expires_at = ?;",
                 ("2000-01-01T00:00:00Z",))
    conn.commit()
    conn.close()
    token = csrf(client, "/forgot-password/verify")
    resp = client.post("/forgot-password/verify",
                       data={"code": sent[0]["code"], "_csrf_token": token},
                       follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/forgot-password/verify")
    assert _pending_count(db_path) == 0


def test_full_reset_flow_with_new_password(brevo_ctx):
    client, db_path, sent = brevo_ctx
    _request_code(client)
    token = csrf(client, "/forgot-password/verify")
    resp = client.post("/forgot-password/verify",
                       data={"code": sent[0]["code"], "_csrf_token": token},
                       follow_redirects=False)
    reset_url = resp.headers["Location"]
    token = csrf(client, reset_url)
    resp = client.post(reset_url, data={
        "new_password": "BrandNewPass123!",
        "confirm_password": "BrandNewPass123!",
        "_csrf_token": token,
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")
    login_token = csrf(client, "/login")
    resp = client.post("/login", data={
        "login_input": "farmer", "password": "BrandNewPass123!",
        "_csrf_token": login_token,
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" not in resp.headers["Location"]
