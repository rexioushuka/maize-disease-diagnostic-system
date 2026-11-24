import csv
import hashlib
import io
import os
import re
import sqlite3
import time
from datetime import timedelta

import numpy as np
import pytest
from PIL import Image
from werkzeug.security import check_password_hash

from msds.db import connect
from msds.security_core import utc_stamp, utcnow

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_IMAGES = os.path.join(BASE, "data", "processed", "test")
CLASSES = ["Downy_Mildew", "Healthy", "MLN", "MSV"]


def _csrf(client, path):
    html = client.get(path).get_data(as_text=True)
    match = re.search(r'name="_csrf_token"\s+value="([^"]+)"', html)
    if not match:
        match = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    assert match, f"No CSRF token on {path}"
    return match.group(1)


def _login(client, username, password="CorrectHorse123!"):
    return client.post(
        "/login",
        data={"login_input": username, "password": password,
              "_csrf_token": _csrf(client, "/login")},
        follow_redirects=False,
    )


def _db(app):
    return connect(app.config["DB_PATH"])


def _events(db, code, user_id=None, outcome=None):
    sql = ("SELECT l.* FROM system_log l JOIN event_type e "
           "ON l.event_type_id = e.event_type_id WHERE e.event_code = ?")
    params = [code]
    if user_id is not None:
        sql += " AND l.user_id = ?"
        params.append(user_id)
    if outcome:
        sql += " AND l.outcome = ?"
        params.append(outcome)
    return db.execute(sql, params).fetchall()


def _png_bytes(colour=(120, 160, 90), size=224):
    image = Image.new("RGB", (size, size), colour)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _test_image_bytes(label):
    folder = os.path.join(TEST_IMAGES, label)
    candidates = [os.path.join(folder, name) for name in os.listdir(folder)
                  if name.lower().endswith((".jpg", ".jpeg", ".png"))]
    path = min(candidates, key=os.path.getsize)
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((224, 224))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=80)
        return output.getvalue()


def _seed_advisories(app):
    db = _db(app)
    for class_id in range(4):
        for language in ("en", "ny"):
            db.execute(
                "INSERT OR IGNORE INTO advisory "
                "(class_id, language_code, status_text, description, action_text, source_ref) "
                "VALUES (?, ?, ?, ?, ?, ?);",
                (class_id, language, "Test status", "Test description",
                 "Test action", "Test IPM source"),
            )
    db.commit()
    db.close()


def _upload(client, payload, filename="leaf.png", endpoint="/api/v1/predict"):
    token = _csrf(client, "/farmer/dashboard")
    return client.post(
        endpoint,
        data={"image": (io.BytesIO(payload), filename), "section_id": "1",
              "_csrf_token": token},
        content_type="multipart/form-data",
    )


def test_TC01_register_farmer_and_hash_password(client, app):
    response = client.post(
        "/register",
        data={"full_name": "New Farmer", "username": "newfarmer01",
              "contact": "newfarmer01@example.com", "password": "StrongPass123!",
              "confirm_password": "StrongPass123!", "section_id": "1",
              "_csrf_token": _csrf(client, "/register")},
        follow_redirects=False,
    )
    assert response.status_code == 302
    db = _db(app)
    row = db.execute("SELECT * FROM app_user WHERE username='newfarmer01';").fetchone()
    assert row is not None
    assert row["role_id"] == 1
    assert row["password_hash"] != "StrongPass123!"
    assert check_password_hash(row["password_hash"], "StrongPass123!")
    assert _events(db, "REGISTER_FARMER", row["user_id"], "SUCCESS")


@pytest.mark.parametrize("forced_role", ["ADMIN", "EXTENSION_OFFICER"])
def test_TC02_signup_role_value_is_ignored(client, app, forced_role):
    username = "rolecheck_" + forced_role.lower()
    client.post(
        "/register",
        data={"full_name": "Role Check", "username": username,
              "contact": f"{username}@example.com", "password": "StrongPass123!",
              "confirm_password": "StrongPass123!", "section_id": "1",
              "role_id": forced_role, "role": forced_role,
              "_csrf_token": _csrf(client, "/register")},
        follow_redirects=False,
    )
    row = _db(app).execute(
        "SELECT role_id FROM app_user WHERE username=?;", (username,)).fetchone()
    assert row["role_id"] == 1


def test_TC03_officer_direct_insert_without_creator_blocked(app):
    db = _db(app)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO app_user (full_name, username, email, password_hash, role_id) "
            "VALUES ('Rogue', 'rogue_officer', 'rogue@example.com', 'hash', 2);"
        )
        db.commit()


def test_TC04_officer_activation_valid_reuse_expired_wrong_contact(client, app):
    db = _db(app)
    admin_id = db.execute(
        "SELECT user_id FROM app_user WHERE username='admin';").fetchone()[0]
    for label, contact, issued, expires in (
        ("valid", "officer.new@example.com", "2020-01-01T00:00:00.000Z",
         "2099-01-01T00:00:00.000Z"),
        ("expired", "officer.old@example.com", "2020-01-01T00:00:00.000Z",
         "2020-02-01T00:00:00.000Z"),
    ):
        code = f"MUBAS-OFF-{label.upper()}-1234"
        db.execute(
            "INSERT INTO officer_invitation (invitee_name, invitee_contact, "
            "employee_ref, role_id, token_hash, issued_by, issued_at, expires_at) "
            "VALUES (?, ?, ?, 2, ?, ?, ?, ?);",
            (label, contact, "REF-001", hashlib.sha256(code.encode()).hexdigest(),
             admin_id, issued, expires),
        )
    db.commit()

    def activate(code, contact, username):
        return client.post(
            "/activate",
            data={"code": code, "contact": contact, "username": username,
                  "full_name": "Activated Officer", "password": "StrongPass123!",
                  "_csrf_token": _csrf(client, "/activate")},
            follow_redirects=False,
        )

    assert activate("MUBAS-OFF-VALID-1234", "officer.new@example.com",
                    "activated_officer").status_code == 302
    row = db.execute(
        "SELECT u.role_id, i.status FROM app_user u JOIN officer_invitation i "
        "ON i.used_by = u.user_id WHERE u.username='activated_officer';").fetchone()
    assert tuple(row) == (2, "USED")
    assert activate("MUBAS-OFF-VALID-1234", "officer.new@example.com",
                    "reused_officer").status_code == 302
    assert activate("MUBAS-OFF-EXPIRED-1234", "officer.old@example.com",
                    "expired_officer").status_code == 302
    assert activate("MUBAS-OFF-VALID-1234", "wrong.contact@example.com",
                    "wrong_contact_officer").status_code == 302
    for username in ("reused_officer", "expired_officer", "wrong_contact_officer"):
        assert db.execute(
            "SELECT 1 FROM app_user WHERE username=?;", (username,)).fetchone() is None


def test_TC05_lockout_after_five_failures_and_recovery(client, app):
    for _ in range(5):
        response = _login(client, "farmer", "WrongPassword123!")
        assert response.status_code == 200
    db = _db(app)
    row = db.execute(
        "SELECT account_status, failed_attempts FROM app_user WHERE username='farmer';"
    ).fetchone()
    assert row["account_status"] == "LOCKED"
    assert row["failed_attempts"] >= 5
    row_id = db.execute(
        "SELECT user_id FROM app_user WHERE username='farmer';").fetchone()[0]
    assert _events(db, "ACCOUNT_LOCKED", row_id)
    db.close()
    assert _login(client, "farmer").status_code == 200
    db = _db(app)
    db.execute(
        "UPDATE app_user SET account_status='ACTIVE', failed_attempts=0, "
        "locked_until=NULL WHERE user_id=?;", (row_id,))
    db.commit()
    db.close()
    assert _login(client, "farmer").status_code == 302
    with client.session_transaction() as session:
        assert session.get("user_id") == row_id


def test_TC06_idle_session_expires_after_thirty_minutes(client):
    assert _login(client, "farmer").status_code == 302
    with client.session_transaction() as session:
        session["_last_activity"] = utc_stamp(utcnow() - timedelta(minutes=31))
    response = client.get("/farmer/dashboard")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_TC07_farmer_is_denied_officer_and_admin_pages(client, app):
    _login(client, "farmer")
    db = _db(app)
    before = db.execute("SELECT COUNT(*) FROM system_log;").fetchone()[0]
    db.close()
    for path in ("/officer/dashboard", "/admin/dashboard", "/officer/export"):
        assert client.get(path).status_code == 403
    assert client.post(
        "/admin/audit/verify",
        data={"_csrf_token": _csrf(client, "/farmer/dashboard")},
    ).status_code == 403
    db = _db(app)
    denied = db.execute(
        "SELECT COUNT(*) FROM system_log l JOIN event_type e "
        "ON l.event_type_id = e.event_type_id "
        "WHERE e.event_code='UNAUTHORIZED_ATTEMPT' AND l.outcome='DENIED';"
    ).fetchone()[0]
    assert denied > 0
    assert db.execute("SELECT COUNT(*) FROM system_log;").fetchone()[0] > before


def test_TC08_invalid_uploads_are_rejected_without_crashing(client):
    _login(client, "farmer")
    oversized = b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024)
    corrupt_png = b"\x89PNG\r\n\x1a\n" + b"not-a-real-png"
    for payload, filename in (
        (b"MZ\x90\x00fake executable", "leaf.exe.png"),
        (b"", "empty.png"),
        (oversized, "large.jpg"),
        (corrupt_png, "corrupt.png"),
    ):
        response = _upload(client, payload, filename)
        assert response.status_code in (400, 413)
        assert response.get_json()["success"] is False
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("label", CLASSES)
def test_TC09_each_class_image_returns_class_confidence_and_advisory(client, app, label):
    _seed_advisories(app)
    _login(client, "farmer")
    response = _upload(client, _test_image_bytes(label), f"{label}.jpg")
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["diagnosis"]["class_name"] in CLASSES
    assert 0 <= body["diagnosis"]["confidence"] <= 100
    assert body["diagnosis"]["description"]
    assert body["diagnosis"]["source"]
    assert len(body["diagnosis"]["probabilities"]) == 4


def test_TC10_low_confidence_and_ood_are_flagged(client, app, monkeypatch):
    class FlatModel:
        def predict(self, batch, verbose=0):
            return np.full((1, 4), 0.25, dtype="float32")

    _login(client, "farmer")
    monkeypatch.setitem(app.extensions, "maize_model", FlatModel())
    response = _upload(client, _png_bytes(), "non_maize.png")
    assert response.status_code == 200
    diagnosis = response.get_json()["diagnosis"]
    assert diagnosis["low_confidence"] is True
    assert diagnosis["is_ood"] is True
    db = _db(app)
    saved = db.execute(
        "SELECT saved_to_history FROM diagnosis WHERE diagnosis_id=?;",
        (diagnosis["id"],)).fetchone()[0]
    assert saved == 0


def test_TC12_history_is_visible_only_to_its_owner(client, app):
    db = _db(app)
    farmer_id = db.execute(
        "SELECT user_id FROM app_user WHERE username='farmer';").fetchone()[0]
    for index in range(20):
        db.execute(
            "INSERT INTO diagnosis (user_id, class_id, image_sha256, model_version, "
            "inference_ms, total_ms) VALUES (?, 1, ?, 'test', 0, 0);",
            (farmer_id, f"{index:064x}"),
        )
    db.commit()
    db.close()
    _login(client, "farmer")
    payload = _png_bytes((int(time.time()) % 200, 90, 60))
    response = _upload(client, payload, "owner.jpg")
    diagnosis_id = response.get_json()["diagnosis"]["id"]
    marker = f"<td>#{diagnosis_id}</td>"
    html = client.get("/farmer/dashboard").get_data(as_text=True)
    assert marker in html
    client.post("/logout", data={"_csrf_token": _csrf(client, "/farmer/dashboard")})
    _login(client, "officer")
    assert marker not in client.get("/farmer/dashboard").get_data(as_text=True)
    client.post("/logout", data={"_csrf_token": _csrf(client, "/farmer/dashboard")})
    _login(client, "admin")
    assert marker not in client.get("/admin/dashboard").get_data(as_text=True)


def test_TC13_officer_totals_match_database_and_csv_is_anonymous(client, app):
    _login(client, "farmer")
    _upload(client, _png_bytes(), "counted.jpg")
    db = _db(app)
    expected = db.execute("SELECT COUNT(*) FROM diagnosis;").fetchone()[0]
    client.post("/logout", data={"_csrf_token": _csrf(client, "/farmer/dashboard")})
    _login(client, "officer")
    dashboard = client.get("/officer/dashboard")
    assert dashboard.status_code == 200
    assert str(expected) in dashboard.get_data(as_text=True)
    export = client.get("/officer/export")
    assert export.status_code == 200
    rows = list(csv.reader(io.StringIO(export.get_data(as_text=True))))
    assert len(rows) == expected + 1
    assert "email" not in rows[0]
    text = export.get_data(as_text=True).lower()
    for secret in ("f@example.com", "o@example.com", "127.0.0.1", "correcthorse123!"):
        assert secret not in text


def test_TC14_log_table_is_append_only_and_chain_verifies(client, app):
    _login(client, "admin")
    db = _db(app)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE system_log SET detail='tampered';")
        db.commit()
    db.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM system_log;")
        db.commit()
    db.rollback()
    db.close()
    response = client.post(
        "/admin/audit/verify",
        data={"_csrf_token": _csrf(client, "/admin/dashboard")},
        follow_redirects=False,
    )
    assert response.status_code == 302
    audit_db = _db(app)
    assert _events(audit_db, "AUDIT_VERIFY", outcome="SUCCESS")
    audit_db.close()


def test_TC15_last_active_administrator_cannot_be_deactivated(client, app):
    _login(client, "admin")
    db = _db(app)
    admin_id = db.execute(
        "SELECT user_id FROM app_user WHERE username='admin';").fetchone()[0]
    db.close()
    response = client.post(
        f"/admin/users/{admin_id}/status",
        data={"action": "deactivate",
              "_csrf_token": _csrf(client, "/admin/dashboard")},
        follow_redirects=False,
    )
    assert response.status_code == 302
    follow_up = client.get("/admin/dashboard", follow_redirects=True)
    assert "cannot deactivate your own account" in follow_up.get_data(as_text=True)
    audit_db = _db(app)
    status = audit_db.execute(
        "SELECT account_status FROM app_user WHERE user_id=?;", (admin_id,)).fetchone()[0]
    assert status == "ACTIVE"
    audit_db.close()


def test_TC16_backup_restore_matches_row_counts(client, app, tmp_path, monkeypatch):
    import backup_db

    _login(client, "farmer")
    _upload(client, _png_bytes(), "backup.jpg")
    db = _db(app)
    counts = {
        table: db.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0]
        for table in ("app_user", "diagnosis", "diagnosis_probability", "system_log")
    }
    monkeypatch.setattr(backup_db, "DB_PATH", app.config["DB_PATH"])
    monkeypatch.setattr(backup_db, "BACKUP_DIR", str(tmp_path / "backups"))
    backup_path = backup_db.create_backup(keep=14)
    assert os.path.exists(backup_path)
    db.execute("UPDATE app_user SET account_status='DEACTIVATED' WHERE username='farmer';")
    db.commit()
    started = time.monotonic()
    source = sqlite3.connect(backup_path)
    destination = sqlite3.connect(app.config["DB_PATH"])
    with destination:
        source.backup(destination)
    source.close()
    destination.close()
    elapsed = time.monotonic() - started
    restored = _db(app)
    for table, expected in counts.items():
        assert restored.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0] == expected
    assert restored.execute(
        "SELECT account_status FROM app_user WHERE username='farmer';").fetchone()[0] == "ACTIVE"
    assert elapsed < 30 * 60
