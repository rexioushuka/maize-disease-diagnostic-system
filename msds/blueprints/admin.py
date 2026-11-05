"""Administrator console (FR-16..FR-21), incl. audit chain verification."""

import hashlib
import secrets
import sqlite3
from datetime import timedelta

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, session, url_for,
)
from werkzeug.security import generate_password_hash

from ..db import get_db
from ..security_core import role_required, t, utc_stamp, utcnow
from ..services.audit import log_event, verify_chain
from ..services.notifications import deliver_secret
from ..services.validation import bounded

bp = Blueprint("admin", __name__)


def _active_admin_count(conn, exclude_user_id=None):
    sql = """
        SELECT COUNT(*) FROM app_user u JOIN role r ON u.role_id = r.role_id
        WHERE r.role_name='ADMIN' AND u.account_status='ACTIVE'
    """
    params = []
    if exclude_user_id is not None:
        sql += " AND u.user_id != ?"
        params.append(exclude_user_id)
    return conn.execute(sql, params).fetchone()[0]


@bp.route("/admin/dashboard")
@role_required("ADMIN")
def dashboard():
    q = bounded(request.args.get("q"), 120)
    role_filter = (request.args.get("role") or "").strip()
    status_filter = (request.args.get("status") or "").strip()
    log_user = bounded(request.args.get("log_user"), 120)
    log_event_filter = bounded(request.args.get("log_event"), 60)
    log_outcome = (request.args.get("log_outcome") or "").strip()
    log_from = (request.args.get("log_from") or "").strip()
    log_to = (request.args.get("log_to") or "").strip()

    conn = get_db()
    cursor = conn.cursor()

    user_where = ["1=1"]
    user_params = []
    if q:
        like = f"%{q}%"
        user_where.append(
            "(u.full_name LIKE ? OR u.username LIKE ? OR u.email LIKE ? "
            "OR u.phone_number LIKE ?)")
        user_params.extend([like, like, like, like])
    if role_filter in {"FARMER", "EXTENSION_OFFICER", "ADMIN"}:
        user_where.append("r.role_name = ?")
        user_params.append(role_filter)
    else:
        role_filter = ""
    if status_filter in {"ACTIVE", "DEACTIVATED", "LOCKED"}:
        user_where.append("u.account_status = ?")
        user_params.append(status_filter)
    else:
        status_filter = ""

    users = cursor.execute(
        f"""
        SELECT u.*, r.role_name FROM app_user u
        JOIN role r ON u.role_id = r.role_id
        WHERE {' AND '.join(user_where)} ORDER BY u.user_id;
        """,
        user_params,
    ).fetchall()

    now_utc = utc_stamp()
    invitations = cursor.execute(
        """
        SELECT i.*, issuer.username AS issued_by_name, r.role_name,
               CASE WHEN i.status='PENDING' AND i.expires_at <= ?
                    THEN 'EXPIRED' ELSE i.status END AS display_status
        FROM officer_invitation i
        LEFT JOIN app_user issuer ON i.issued_by = issuer.user_id
        JOIN role r ON i.role_id = r.role_id
        ORDER BY i.issued_at DESC LIMIT 50;
        """,
        (now_utc,),
    ).fetchall()

    officers = cursor.execute(
        """
        SELECT u.user_id, u.full_name, u.username, u.account_status
        FROM app_user u JOIN role r ON u.role_id = r.role_id
        WHERE r.role_name = 'EXTENSION_OFFICER'
        ORDER BY u.full_name;
        """,
    ).fetchall()
    all_sections = cursor.execute(
        "SELECT * FROM village_section ORDER BY section_name;").fetchall()
    officer_sections = {}
    for row in cursor.execute(
            "SELECT user_id, section_id FROM officer_section;").fetchall():
        officer_sections.setdefault(row["user_id"], set()).add(row["section_id"])

    log_where = ["1=1"]
    log_params = []
    for key, op in (("log_from", ">="), ("log_to", "<=")):
        value = {"log_from": log_from, "log_to": log_to}[key]
        if value:
            try:
                from datetime import datetime
                datetime.strptime(value, "%Y-%m-%d")
                log_where.append(f"date(l.occurred_at) {op} date(?)")
                log_params.append(value)
            except ValueError:
                if key == "log_from":
                    log_from = ""
                else:
                    log_to = ""
    if log_user:
        log_where.append("(u.username LIKE ? OR CAST(l.user_id AS TEXT) = ?)")
        log_params.extend([f"%{log_user}%", log_user])
    if log_event_filter:
        log_where.append("e.event_code = ?")
        log_params.append(log_event_filter)
    if log_outcome in {"SUCCESS", "FAILURE", "DENIED"}:
        log_where.append("l.outcome = ?")
        log_params.append(log_outcome)
    else:
        log_outcome = ""

    logs = cursor.execute(
        f"""
        SELECT l.*, e.event_code, u.username
        FROM system_log l
        JOIN event_type e ON l.event_type_id = e.event_type_id
        LEFT JOIN app_user u ON l.user_id = u.user_id
        WHERE {' AND '.join(log_where)}
        ORDER BY l.occurred_at DESC, l.log_id DESC LIMIT 200;
        """,
        log_params,
    ).fetchall()

    event_types = cursor.execute(
        "SELECT * FROM event_type ORDER BY event_code;").fetchall()
    total_users = cursor.execute("SELECT COUNT(*) FROM app_user;").fetchone()[0]
    active_users = cursor.execute(
        "SELECT COUNT(*) FROM app_user WHERE account_status='ACTIVE';").fetchone()[0]
    total_diagnoses = cursor.execute("SELECT COUNT(*) FROM diagnosis;").fetchone()[0]
    pending_invites = cursor.execute(
        "SELECT COUNT(*) FROM officer_invitation "
        "WHERE status='PENDING' AND expires_at > ?;", (now_utc,)).fetchone()[0]
    chain_ok, chain_bad = verify_chain(conn)

    log_event("LOG_VIEW", "SUCCESS", user_id=session["user_id"])

    return render_template(
        "admin.html", total_users=total_users, active_users=active_users,
        total_diagnoses=total_diagnoses, pending_invites=pending_invites,
        users=users, invitations=invitations, logs=logs,
        event_types=event_types, q=q, role_filter=role_filter,
        status_filter=status_filter, log_user=log_user,
        log_event=log_event_filter, log_outcome=log_outcome,
        log_from=log_from, log_to=log_to, chain_ok=chain_ok, chain_bad=chain_bad,
        officers=officers, all_sections=all_sections,
        officer_sections=officer_sections,
    )


INVITABLE_ROLES = {"EXTENSION_OFFICER": 2, "ADMIN": 3}
INVITE_PREFIX = {"EXTENSION_OFFICER": "MUBAS-OFF-", "ADMIN": "MUBAS-ADM-"}


@bp.route("/admin/invite", methods=["POST"])
@role_required("ADMIN")
def invite_officer():
    invitee_name = bounded(request.form.get("invitee_name"), 120)
    invitee_contact = bounded(request.form.get("invitee_contact"), 120)
    employee_ref = bounded(request.form.get("employee_ref"), 60)
    invitee_role = (request.form.get("invitee_role") or "EXTENSION_OFFICER").strip()

    if invitee_role not in INVITABLE_ROLES:
        flash(t("msg_no_permission"), "danger")
        return redirect(url_for("admin.dashboard"))
    if not all([invitee_name, invitee_contact, employee_ref]):
        flash(t("msg_fields_required"), "danger")
        return redirect(url_for("admin.dashboard"))

    role_id = INVITABLE_ROLES[invitee_role]
    raw_code = f"{INVITE_PREFIX[invitee_role]}{secrets.token_hex(16).upper()}"
    token_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
    expires_at = utc_stamp(utcnow() + timedelta(hours=72))

    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO officer_invitation
                (invitee_name, invitee_contact, employee_ref, role_id,
                 token_hash, issued_by, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (invitee_name, invitee_contact, employee_ref, role_id, token_hash,
             session["user_id"], expires_at),
        )
        conn.commit()
        # SEC-15: deliver out of band; never echo the code in the UI.
        delivered = deliver_secret(
            invitee_contact, "Maize system account invitation",
            f"One-time activation code: {raw_code}")
        log_event("INVITATION_ISSUED", "SUCCESS", user_id=session["user_id"],
                  detail=f"Issued {invitee_role} invitation for {invitee_name}")
        flash(t("msg_invite_success_delivered" if delivered
                else "msg_invite_success_pending"), "success")
    except sqlite3.Error as exc:
        conn.rollback()
        log_event("INVITATION_ISSUED", "FAILURE", user_id=session["user_id"],
                  detail=str(exc))
        flash(t("msg_invite_error"), "danger")

    return redirect(url_for("admin.dashboard"))


@bp.route("/admin/invitations/<int:invitation_id>/revoke", methods=["POST"])
@role_required("ADMIN")
def revoke_invitation(invitation_id):
    conn = get_db()
    invite = conn.execute(
        "SELECT * FROM officer_invitation WHERE invitation_id = ?;",
        (invitation_id,),
    ).fetchone()
    if not invite or invite["status"] != "PENDING":
        flash(t("msg_no_permission"), "danger")
        return redirect(url_for("admin.dashboard"))

    conn.execute(
        "UPDATE officer_invitation SET status='REVOKED' WHERE invitation_id=?;",
        (invitation_id,),
    )
    conn.commit()
    log_event("INVITATION_REVOKED", "SUCCESS", user_id=session["user_id"],
              detail=f"Revoked invitation {invitation_id}")
    flash(t("msg_invite_revoked"), "success")
    return redirect(url_for("admin.dashboard"))


@bp.route("/admin/officers/<int:user_id>/sections", methods=["POST"])
@role_required("ADMIN")
def set_officer_sections(user_id):
    conn = get_db()
    target = conn.execute(
        """
        SELECT u.user_id FROM app_user u
        JOIN role r ON u.role_id = r.role_id
        WHERE u.user_id = ? AND r.role_name = 'EXTENSION_OFFICER';
        """,
        (user_id,),
    ).fetchone()
    if not target:
        abort(404)

    valid_ids = {row["section_id"] for row in conn.execute(
        "SELECT section_id FROM village_section;").fetchall()}
    chosen = set()
    for raw in request.form.getlist("section_ids"):
        if raw.isdigit() and int(raw) in valid_ids:
            chosen.add(int(raw))

    try:
        conn.execute("DELETE FROM officer_section WHERE user_id = ?;",
                     (user_id,))
        conn.executemany(
            "INSERT INTO officer_section (user_id, section_id) VALUES (?, ?);",
            [(user_id, sid) for sid in sorted(chosen)],
        )
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        log_event("USER_STATUS_CHANGE", "FAILURE", user_id=session["user_id"],
                  target_user_id=user_id, detail=str(exc))
        flash(t("msg_sections_error"), "danger")
        return redirect(url_for("admin.dashboard"))

    log_event("USER_STATUS_CHANGE", "SUCCESS", user_id=session["user_id"],
              target_user_id=user_id,
              detail=f"Sections set to {sorted(chosen)}")
    flash(t("msg_sections_saved"), "success")
    return redirect(url_for("admin.dashboard"))


@bp.route("/admin/users/<int:user_id>/status", methods=["POST"])
@role_required("ADMIN")
def set_user_status(user_id):
    action = request.form.get("action")
    if action not in {"activate", "deactivate"}:
        abort(400)

    conn = get_db()
    target = conn.execute(
        """
        SELECT u.*, r.role_name FROM app_user u
        JOIN role r ON u.role_id = r.role_id WHERE u.user_id = ?;
        """,
        (user_id,),
    ).fetchone()
    if not target:
        abort(404)

    if action == "deactivate":
        if user_id == session["user_id"]:
            flash(t("msg_self_deactivate"), "danger")
            return redirect(url_for("admin.dashboard"))
        if target["role_name"] == "ADMIN" and _active_admin_count(conn, user_id) == 0:
            log_event("USER_STATUS_CHANGE", "DENIED", user_id=session["user_id"],
                      target_user_id=user_id,
                      detail="Attempt to deactivate last active Administrator")
            flash(t("msg_last_admin"), "danger")
            return redirect(url_for("admin.dashboard"))
        new_status, message = "DEACTIVATED", t("msg_user_deactivated")
    else:
        new_status, message = "ACTIVE", t("msg_user_activated")

    conn.execute(
        "UPDATE app_user SET account_status=?, failed_attempts=0, locked_until=NULL "
        "WHERE user_id=?;",
        (new_status, user_id),
    )
    conn.commit()
    log_event("USER_STATUS_CHANGE", "SUCCESS", user_id=session["user_id"],
              target_user_id=user_id, detail=f"Status set to {new_status}")
    flash(message, "success")
    return redirect(url_for("admin.dashboard"))


@bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@role_required("ADMIN")
def reset_user_password(user_id):
    conn = get_db()
    target = conn.execute(
        "SELECT * FROM app_user WHERE user_id = ?;", (user_id,)
    ).fetchone()
    if not target:
        abort(404)

    # SEC-15: do not display the temporary password. Deliver it out of band.
    temporary_password = "Mb-" + secrets.token_urlsafe(18)
    conn.execute(
        """
        UPDATE app_user
        SET password_hash=?, must_change_password=1, failed_attempts=0,
            locked_until=NULL
        WHERE user_id=?;
        """,
        (generate_password_hash(temporary_password, method="pbkdf2:sha256"), user_id),
    )
    conn.commit()
    delivered = deliver_secret(
        target["email"] or target["phone_number"],
        "Maize system password reset",
        f"Temporary password: {temporary_password}")
    log_event("PASSWORD_RESET", "SUCCESS", user_id=session["user_id"],
              target_user_id=user_id, detail="Administrator reset password")
    flash(t("msg_password_reset_delivered" if delivered
            else "msg_password_reset_pending"), "success")
    return redirect(url_for("admin.dashboard"))


@bp.route("/admin/audit/verify", methods=["POST"])
@role_required("ADMIN")
def verify_audit():
    conn = get_db()
    ok, bad = verify_chain(conn)
    log_event("AUDIT_VERIFY", "SUCCESS" if ok else "FAILURE",
              user_id=session["user_id"],
              detail=f"chain_ok={ok}, first_bad={bad}")
    flash(t("msg_audit_intact") if ok else t("msg_audit_broken", id=bad),
          "success" if ok else "danger")
    return redirect(url_for("admin.dashboard"))
