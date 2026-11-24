import os
import sqlite3

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(BASE, "maize_schema.sql")


@pytest.fixture()
def conn(tmp_path):
    path = str(tmp_path / "safeguards.db")
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON;")
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        db.executescript(fh.read())
    db.execute(
        "INSERT INTO village_section (section_name, epa_name) VALUES ('Nzumala', 'Liwaladzi');"
    )
    db.commit()
    yield db
    db.close()


def _insert_user(db, username, role_id, created_by=None, email=None, phone=None):
    cursor = db.execute(
        "INSERT INTO app_user (full_name, username, email, phone_number, "
        "password_hash, role_id, created_by) VALUES (?, ?, ?, ?, ?, ?, ?);",
        ("Test User", username, email or f"{username}@example.com", phone,
         "pbkdf2:sha256:dummy", role_id, created_by),
    )
    db.commit()
    return cursor.lastrowid


def _attempt(operation):
    try:
        operation()
    except sqlite3.IntegrityError as exc:
        return "Blocked", str(exc)
    return "Allowed", ""


def _check_db01(db):
    expected = "Allowed"
    actual, _ = _attempt(lambda: _insert_user(db, "bootstrap_admin", 3))
    return expected, actual, "Pass" if actual == expected else "Fail"


def _check_db02(db):
    expected = "Allowed"
    actual, _ = _attempt(lambda: _insert_user(db, "farmer_one", 1))
    return expected, actual, "Pass" if actual == expected else "Fail"


def _check_db03(db):
    _insert_user(db, "admin_for_officer", 3)
    expected = "Blocked"
    actual, _ = _attempt(lambda: _insert_user(db, "rogue_officer", 2))
    return expected, actual, "Pass" if actual == expected else "Fail"


def _check_db04(db):
    _insert_user(db, "first_admin", 3)
    expected = "Blocked"
    actual, _ = _attempt(lambda: _insert_user(db, "second_admin", 3))
    return expected, actual, "Pass" if actual == expected else "Fail"


def _check_db05(db):
    admin_id = _insert_user(db, "inviting_admin", 3)
    expected = "Allowed"
    actual, _ = _attempt(lambda: _insert_user(db, "vetted_officer", 2, admin_id))
    return expected, actual, "Pass" if actual == expected else "Fail"


def _log_id(db):
    cursor = db.execute(
        "INSERT INTO system_log (event_type_id, outcome, source_ip, request_path, detail) "
        "VALUES (1, 'SUCCESS', '127.0.0.1', '/login', 'check');"
    )
    db.commit()
    return cursor.lastrowid


def _check_db06(db):
    expected = "Allowed"
    before = db.execute("SELECT COUNT(*) FROM system_log;").fetchone()[0]
    actual, _ = _attempt(lambda: _log_id(db))
    after = db.execute("SELECT COUNT(*) FROM system_log;").fetchone()[0]
    if after != before + 1:
        actual = "Not inserted"
    return expected, actual, "Pass" if actual == expected else "Fail"


def _check_db07(db):
    log_id = _log_id(db)
    expected = "Blocked"
    actual, _ = _attempt(
        lambda: (db.execute("UPDATE system_log SET detail='changed' WHERE log_id=?;",
                            (log_id,)), db.commit())
    )
    return expected, actual, "Pass" if actual == expected else "Fail"


def _check_db08(db):
    log_id = _log_id(db)
    expected = "Blocked"
    actual, _ = _attempt(
        lambda: (db.execute("DELETE FROM system_log WHERE log_id=?;", (log_id,)),
                 db.commit())
    )
    return expected, actual, "Pass" if actual == expected else "Fail"


def _check_db09(db):
    expected = "Blocked"
    actual, _ = _attempt(
        lambda: db.execute(
            "INSERT INTO app_user (full_name, username, password_hash, role_id) "
            "VALUES ('No Contact', 'no_contact', 'hash', 1);"
        )
    )
    return expected, actual, "Pass" if actual == expected else "Fail"


def _check_db10(db):
    user_id = _insert_user(db, "probability_user", 1)
    cursor = db.execute(
        "INSERT INTO diagnosis (user_id, class_id, image_sha256, model_version, "
        "inference_ms, total_ms) VALUES (?, 1, ?, 'test', 0, 0);",
        (user_id, "a" * 64),
    )
    diagnosis_id = cursor.lastrowid
    db.commit()
    expected = "Blocked"
    actual, _ = _attempt(
        lambda: db.execute(
            "INSERT INTO diagnosis_probability (diagnosis_id, class_id, probability) "
            "VALUES (?, 1, 1.5);", (diagnosis_id,)
        )
    )
    return expected, actual, "Pass" if actual == expected else "Fail"


def _check_db11(db):
    _insert_user(db, "CaseSensitive", 1)
    expected = "Blocked"
    actual, _ = _attempt(lambda: _insert_user(db, "casesensitive", 1))
    return expected, actual, "Pass" if actual == expected else "Fail"


def _check_db12(db):
    expected = "Blocked"
    actual, _ = _attempt(
        lambda: db.execute(
            "INSERT INTO diagnosis (user_id, class_id, image_sha256, model_version, "
            "inference_ms, total_ms) VALUES (999999, 1, ?, 'test', 0, 0);",
            ("b" * 64,),
        )
    )
    return expected, actual, "Pass" if actual == expected else "Fail"


CHECKS = [
    ("DB-01", "Bootstrap first administrator", _check_db01),
    ("DB-02", "Self-registered farmer", _check_db02),
    ("DB-03", "Officer without creator", _check_db03),
    ("DB-04", "Self-registered administrator after the first", _check_db04),
    ("DB-05", "Officer created by an administrator", _check_db05),
    ("DB-06", "Insert a log entry", _check_db06),
    ("DB-07", "Update a log entry", _check_db07),
    ("DB-08", "Delete a log entry", _check_db08),
    ("DB-09", "User with neither e-mail nor phone", _check_db09),
    ("DB-10", "Probability above 1", _check_db10),
    ("DB-11", "Duplicate username in different letter case", _check_db11),
    ("DB-12", "Diagnosis for a user that does not exist", _check_db12),
]


@pytest.mark.parametrize("check_id,description,check", CHECKS,
                         ids=[item[0] for item in CHECKS])
def test_db_safeguard(conn, check_id, description, check):
    expected, actual, status = check(conn)
    assert status == "Pass", f"{check_id}: expected {expected}, got {actual}"
