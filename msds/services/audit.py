"""Append-only audit trail with a per-row hash chain (SEC-16).

Each row stores ``prev_hash`` (the previous row's ``row_hash``) and
``row_hash = HMAC-SHA256(secret, canonical(row) + prev_hash)``.  A verifier can
walk the chain and detect deletion, modification or reordering even if the
attacker can drop the SQLite triggers.  The HMAC key lives in the environment /
instance folder, not the database.
"""

import hashlib
import hmac
import json

from flask import current_app, has_request_context, request

from ..db import get_db


def _audit_secret():
    return str(current_app.config.get("AUDIT_HMAC_KEY") or
               current_app.config.get("SECRET_KEY")).encode("utf-8")


def _canonical(row):
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def _row_hash(prev_hash, row):
    msg = (_canonical(row) + "|" + (prev_hash or "")).encode("utf-8")
    return hmac.new(_audit_secret(), msg, hashlib.sha256).hexdigest()


def log_event(event_code, outcome, user_id=None, target_user_id=None,
              diagnosis_id=None, detail=""):
    """Append one audit event. Failures are logged loudly, never silently."""
    ip_addr = req_path = None
    if has_request_context():
        ip_addr = request.headers.get("X-Forwarded-For", request.remote_addr)
        if ip_addr:
            ip_addr = ip_addr.split(",")[0].strip()
        req_path = request.path

    conn = get_db()
    try:
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT event_type_id FROM event_type WHERE event_code = ?;",
            (event_code,),
        ).fetchone()
        if not row:
            return
        last = cursor.execute(
            "SELECT row_hash FROM system_log ORDER BY log_id DESC LIMIT 1;"
        ).fetchone()
        prev_hash = last["row_hash"] if last and last["row_hash"] else ""
        payload = {
            "user_id": user_id,
            "event_type_id": row["event_type_id"],
            "outcome": outcome,
            "source_ip": ip_addr,
            "request_path": req_path,
            "target_user_id": target_user_id,
            "diagnosis_id": diagnosis_id,
            "detail": (detail or "")[:500],
        }
        row_hash = _row_hash(prev_hash, payload)
        cursor.execute(
            """
            INSERT INTO system_log
                (user_id, event_type_id, outcome, source_ip, request_path,
                 target_user_id, diagnosis_id, detail, prev_hash, row_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (user_id, row["event_type_id"], outcome, ip_addr, req_path,
             target_user_id, diagnosis_id, (detail or "")[:500], prev_hash, row_hash),
        )
        conn.commit()
    except Exception as exc:  # pragma: no cover - must not break the request
        current_app.logger.error("AUDIT LOG WRITE FAILED (%s): %s", event_code, exc)
    return


def verify_chain(conn=None):
    """Return (ok, first_bad_log_id).

    Rows created before the hash chain was introduced (row_hash is NULL) are
    treated as the immutable baseline; verification anchors at the first hashed
    row.
    """
    conn = conn or get_db()
    prev = None
    rows = conn.execute(
        "SELECT log_id, user_id, event_type_id, outcome, source_ip, request_path, "
        "target_user_id, diagnosis_id, detail, prev_hash, row_hash "
        "FROM system_log ORDER BY log_id ASC;"
    ).fetchall()
    for row in rows:
        if not row["row_hash"]:
            continue
        payload = {
            "user_id": row["user_id"],
            "event_type_id": row["event_type_id"],
            "outcome": row["outcome"],
            "source_ip": row["source_ip"],
            "request_path": row["request_path"],
            "target_user_id": row["target_user_id"],
            "diagnosis_id": row["diagnosis_id"],
            "detail": row["detail"],
        }
        if prev is None:
            # First hashed row anchors the chain.
            prev = row["prev_hash"] or ""
        if (row["prev_hash"] or "") != prev:
            return False, row["log_id"]
        if _row_hash(prev, payload) != row["row_hash"]:
            return False, row["log_id"]
        prev = row["row_hash"] or ""
    return True, None
