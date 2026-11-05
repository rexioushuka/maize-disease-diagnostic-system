"""Authentication routes: register, login, logout, password, activation.

Fixes applied here: SEC-06 (token-based reset, no enumeration), SEC-07 (POST
logout, CSRF rotation), SEC-13/SEC-14 (lockout + policy), SEC-15 (no secrets in
the UI), SEC-17 (validated inputs).
"""

import hashlib
import hmac
import secrets
import sqlite3
from datetime import timedelta

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import get_db
from ..extensions import rate_limiter
from ..security_core import (
    allow_request, role_required, rotate_csrf_token, t, utc_stamp, utcnow,
)
from ..services import brevo
from ..services.audit import log_event
from ..services.notifications import deliver_secret
from ..services.validation import (
    bounded, parse_contact, password_errors, valid_full_name, valid_username,
)

bp = Blueprint("auth", __name__)

GENERIC_LOGIN_ERROR = "msg_invalid_credentials"
GENERIC_RESET_RESPONSE = "msg_recovery_requested"
VERIFY_TTL_MINUTES = 10
VERIFY_MAX_ATTEMPTS = 5


def _mask_contact(kind, value):
    if kind == "email":
        local, sep, domain = value.partition("@")
        if sep:
            return f"{local[:1]}***@{domain}"
        return "***"
    if len(value) > 7:
        return f"{value[:4]}****{value[-3:]}"
    return "****"


def _pending_verification(conn, public_id):
    return conn.execute(
        "SELECT * FROM registration_verification WHERE public_id = ?;",
        (public_id,),
    ).fetchone()


def _validate_section(raw):
    if raw in (None, ""):
        return None, False
    try:
        section_id = int(raw)
    except (TypeError, ValueError):
        return None, False
    row = get_db().execute(
        "SELECT section_id FROM village_section WHERE section_id = ?;", (section_id,)
    ).fetchone()
    return (section_id, True) if row else (None, False)


def _password_problem(password):
    errors = password_errors(password)
    if not errors:
        return None
    if errors == ["min_length"]:
        return "msg_password_short"
    return "msg_password_weak"


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Public farmer registration only (FR-01, FR-02)."""
    from flask import current_app

    if request.method == "POST":
        full_name = bounded(request.form.get("full_name"), 120)
        username = bounded(request.form.get("username"), 32)
        contact = bounded(request.form.get("contact"), 120)
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password")

        if not all([full_name, username, contact, password]):
            flash(t("msg_fields_required"), "danger")
            return redirect(url_for("auth.register"))
        if not valid_full_name(full_name):
            flash(t("msg_invalid_name"), "danger")
            return redirect(url_for("auth.register"))
        if not valid_username(username):
            flash(t("msg_invalid_username"), "danger")
            return redirect(url_for("auth.register"))
        problem = _password_problem(password)
        if problem:
            flash(t(problem), "danger")
            return redirect(url_for("auth.register"))
        if confirm_password is not None and confirm_password != password:
            flash(t("msg_password_mismatch"), "danger")
            return redirect(url_for("auth.register"))

        contact_kind, contact_value = parse_contact(contact)
        if contact_kind is None:
            flash(t("msg_invalid_contact"), "danger")
            return redirect(url_for("auth.register"))

        section_id, ok = _validate_section(request.form.get("section_id"))
        if not ok or section_id is None:
            flash(t("msg_invalid_section"), "danger")
            return redirect(url_for("auth.register"))

        email = contact_value if contact_kind == "email" else None
        phone = contact_value if contact_kind == "phone" else None
        password_hash = generate_password_hash(password, method="pbkdf2:sha256")

        conn = get_db()
        existing = conn.execute(
            "SELECT 1 FROM app_user WHERE username = ? OR email = ? "
            "OR phone_number = ? LIMIT 1;",
            (username, email, phone),
        ).fetchone()
        if existing:
            log_event("REGISTER_FARMER", "FAILURE",
                      detail=f"Duplicate registration attempt for {username}")
            flash(t("msg_duplicate"), "danger")
            return redirect(url_for("auth.register"))

        # Purge expired pending registrations before creating a new one.
        conn.execute(
            "DELETE FROM registration_verification WHERE expires_at <= ?;",
            (utc_stamp(),),
        )

        if not brevo.brevo_configured():
            # Brevo not configured: fall back to immediate registration so
            # development and test environments still work.
            current_app.logger.warning(
                "BREVO_API_KEY not configured; registration for %s is not "
                "contact-verified.", username)
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO app_user
                        (full_name, username, email, phone_number, password_hash,
                         role_id, section_id)
                    VALUES (?, ?, ?, ?, ?, 1, ?);
                    """,
                    (full_name, username, email, phone, password_hash,
                     section_id),
                )
                conn.commit()
                log_event("REGISTER_FARMER", "SUCCESS",
                          user_id=cursor.lastrowid,
                          detail="Farmer registered (contact not verified)")
                flash(t("msg_registered"), "success")
                return redirect(url_for("auth.login"))
            except sqlite3.IntegrityError:
                conn.rollback()
                flash(t("msg_duplicate"), "danger")
                return redirect(url_for("auth.register"))

        code = f"{secrets.randbelow(1000000):06d}"
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        public_id = secrets.token_urlsafe(16)
        expires_at = utc_stamp(utcnow() + timedelta(minutes=VERIFY_TTL_MINUTES))
        conn.execute(
            "DELETE FROM registration_verification WHERE contact = ?;",
            (contact_value,),
        )
        conn.execute(
            """
            INSERT INTO registration_verification
                (public_id, contact, contact_kind, full_name, username,
                 section_id, password_hash, code_hash, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (public_id, contact_value, contact_kind, full_name, username,
             section_id, password_hash, code_hash, expires_at),
        )
        conn.commit()
        try:
            brevo.send_verification_code(contact_kind, contact_value, code)
        except brevo.BrevoError as exc:
            conn.execute(
                "DELETE FROM registration_verification WHERE public_id = ?;",
                (public_id,),
            )
            conn.commit()
            current_app.logger.error("Brevo verification send failed: %s", exc)
            log_event("REGISTER_FARMER", "FAILURE",
                      detail="Verification code could not be delivered")
            if contact_kind == "phone" and "sms" in str(exc).lower():
                flash(t("msg_sms_unavailable"), "danger")
            else:
                flash(t("msg_code_send_failed"), "danger")
            return redirect(url_for("auth.register"))

        log_event("REGISTER_FARMER", "SUCCESS",
                  detail=f"Verification code sent ({contact_kind})")
        return redirect(url_for("auth.register_verify", public_id=public_id))

    sections = get_db().execute(
        "SELECT * FROM village_section ORDER BY section_name;"
    ).fetchall()
    return render_template("register.html", sections=sections)


def _create_account_from_pending(conn, row):
    cursor = conn.execute(
        """
        INSERT INTO app_user
            (full_name, username, email, phone_number, password_hash,
             role_id, section_id)
        VALUES (?, ?, ?, ?, ?, 1, ?);
        """,
        (row["full_name"], row["username"],
         row["contact"] if row["contact_kind"] == "email" else None,
         row["contact"] if row["contact_kind"] == "phone" else None,
         row["password_hash"], row["section_id"]),
    )
    conn.execute(
        "DELETE FROM registration_verification WHERE verification_id = ?;",
        (row["verification_id"],),
    )
    conn.commit()
    return cursor.lastrowid


@bp.route("/register/verify/<public_id>", methods=["GET", "POST"])
def register_verify(public_id):
    from flask import current_app

    conn = get_db()
    row = _pending_verification(conn, public_id)
    if not row:
        flash(t("msg_code_expired"), "danger")
        return redirect(url_for("auth.register"))

    if row["expires_at"] <= utc_stamp():
        conn.execute(
            "DELETE FROM registration_verification WHERE verification_id = ?;",
            (row["verification_id"],),
        )
        conn.commit()
        flash(t("msg_code_expired"), "danger")
        return redirect(url_for("auth.register"))

    if request.method == "POST":
        if not allow_request("register_verify", 10, 60):
            abort(429)
        code = bounded(request.form.get("code"), 6)
        if row["attempts"] >= VERIFY_MAX_ATTEMPTS:
            conn.execute(
                "DELETE FROM registration_verification WHERE verification_id = ?;",
                (row["verification_id"],),
            )
            conn.commit()
            flash(t("msg_code_too_many"), "danger")
            return redirect(url_for("auth.register"))
        candidate = hashlib.sha256(code.encode("utf-8")).hexdigest() if code else ""
        if hmac.compare_digest(candidate, row["code_hash"]):
            try:
                new_user_id = _create_account_from_pending(conn, row)
            except sqlite3.IntegrityError:
                conn.rollback()
                log_event("REGISTER_FARMER", "FAILURE",
                          detail=f"Duplicate account at verification for {row['username']}")
                flash(t("msg_duplicate"), "danger")
                return redirect(url_for("auth.register"))
            log_event("REGISTER_FARMER", "SUCCESS", user_id=new_user_id,
                      detail="Farmer registered after contact verification")
            flash(t("msg_registered"), "success")
            return redirect(url_for("auth.login"))
        conn.execute(
            "UPDATE registration_verification SET attempts = attempts + 1 "
            "WHERE verification_id = ?;",
            (row["verification_id"],),
        )
        conn.commit()
        flash(t("msg_code_invalid"), "danger")
        return redirect(url_for("auth.register_verify", public_id=public_id))

    return render_template(
        "register_verify.html",
        public_id=public_id,
        masked_contact=_mask_contact(row["contact_kind"], row["contact"]),
    )


@bp.route("/register/verify/<public_id>/resend", methods=["POST"])
def register_verify_resend(public_id):
    from flask import current_app

    if not allow_request("register_resend", 3, 300):
        abort(429)
    conn = get_db()
    row = _pending_verification(conn, public_id)
    if not row or row["expires_at"] <= utc_stamp():
        flash(t("msg_code_expired"), "danger")
        return redirect(url_for("auth.register"))

    code = f"{secrets.randbelow(1000000):06d}"
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    expires_at = utc_stamp(utcnow() + timedelta(minutes=VERIFY_TTL_MINUTES))
    conn.execute(
        "UPDATE registration_verification "
        "SET code_hash = ?, attempts = 0, expires_at = ? "
        "WHERE verification_id = ?;",
        (code_hash, expires_at, row["verification_id"]),
    )
    conn.commit()
    try:
        brevo.send_verification_code(row["contact_kind"], row["contact"], code)
        flash(t("msg_code_resent"), "success")
    except brevo.BrevoError as exc:
        current_app.logger.error("Brevo resend failed: %s", exc)
        if row["contact_kind"] == "phone" and "sms" in str(exc).lower():
            flash(t("msg_sms_unavailable"), "danger")
        else:
            flash(t("msg_code_send_failed"), "danger")
    return redirect(url_for("auth.register_verify", public_id=public_id))


def _login_lookup(login_input):
    return get_db().execute(
        """
        SELECT u.*, r.role_name FROM app_user u
        JOIN role r ON u.role_id = r.role_id
        WHERE u.username = ? OR u.email = ? OR u.phone_number = ?;
        """,
        (login_input, login_input, login_input),
    ).fetchone()


def _is_locked(user):
    if user["account_status"] != "LOCKED":
        return False
    locked_until = user["locked_until"]
    if not locked_until:
        return False
    from ..security_core import parse_utc
    until = parse_utc(locked_until)
    return bool(until and until > utcnow())


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Sign in with username, email or phone (FR-04, FR-05)."""
    if request.method == "POST":
        from flask import current_app
        cfg = current_app.config
        if not allow_request("login", cfg["LOGIN_RATE_LIMIT"],
                             cfg["LOGIN_RATE_WINDOW"]):
            log_event("LOGIN_FAILURE", "DENIED", detail="Login rate limit exceeded")
            abort(429)

        login_input = bounded(request.form.get("login_input"), 120)
        password = request.form.get("password") or ""
        conn = get_db()
        user = _login_lookup(login_input)

        # Uniform failure path: never reveal whether the account exists.
        if user is None or user["account_status"] == "DEACTIVATED":
            log_event("LOGIN_FAILURE", "FAILURE", detail="Failed login attempt")
            flash(t(GENERIC_LOGIN_ERROR), "danger")
            return render_template("login.html")

        if _is_locked(user):
            log_event("LOGIN_FAILURE", "FAILURE", user_id=user["user_id"],
                      detail="Account locked")
            flash(t("msg_account_locked"), "danger")
            return render_template("login.html")

        if user["account_status"] == "LOCKED":
            conn.execute(
                "UPDATE app_user SET account_status='ACTIVE', failed_attempts=0, "
                "locked_until=NULL WHERE user_id=?;",
                (user["user_id"],),
            )
            conn.commit()

        if check_password_hash(user["password_hash"], password):
            conn.execute(
                "UPDATE app_user SET failed_attempts=0, locked_until=NULL, "
                "account_status='ACTIVE', last_login_at=? WHERE user_id=?;",
                (utc_stamp(), user["user_id"]),
            )
            conn.commit()

            selected_lang = session.get("lang", "en")
            user_id = user["user_id"]
            session.clear()
            session.permanent = True
            session["user_id"] = user_id
            session["username"] = user["username"]
            session["full_name"] = user["full_name"]
            session["role_name"] = user["role_name"]
            session["must_change_password"] = bool(user["must_change_password"])
            session["lang"] = selected_lang
            session["_last_activity"] = utc_stamp()
            session["_created_at"] = utc_stamp()
            rotate_csrf_token()
            log_event("LOGIN_SUCCESS", "SUCCESS", user_id=user_id)

            if session["must_change_password"]:
                flash(t("msg_must_change"), "warning")
                return redirect(url_for("auth.change_password"))
            return redirect(url_for("main.index"))

        failed = user["failed_attempts"] + 1
        status = user["account_status"]
        locked_until = None
        if failed >= cfg["LOGIN_MAX_ATTEMPTS"]:
            status = "LOCKED"
            locked_until = utc_stamp(
                utcnow() + timedelta(minutes=cfg["LOGIN_LOCK_MINUTES"])
            )
            log_event("ACCOUNT_LOCKED", "FAILURE", user_id=user["user_id"],
                      detail=f"{cfg['LOGIN_MAX_ATTEMPTS']} consecutive failed logins")
        conn.execute(
            "UPDATE app_user SET failed_attempts=?, locked_until=?, account_status=? "
            "WHERE user_id=?;",
            (failed, locked_until, status, user["user_id"]),
        )
        conn.commit()
        log_event("LOGIN_FAILURE", "FAILURE", user_id=user["user_id"],
                  detail="Failed login attempt")
        flash(t(GENERIC_LOGIN_ERROR), "danger")

    return render_template("login.html")


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    # POST is the supported path (SEC-07); GET is kept only as a no-op redirect
    # for stale bookmarks and never terminates the session.
    if request.method != "POST":
        flash(t("msg_logout_post"), "info")
        return redirect(url_for("main.index"))

    user_id = session.get("user_id")
    if user_id:
        log_event("LOGOUT", "SUCCESS", user_id=user_id)
    session.clear()
    flash(t("msg_logged_out"), "info")
    return redirect(url_for("auth.login"))


@bp.route("/logout/idle", methods=["POST"])
def logout_idle():
    user_id = session.get("user_id")
    if user_id:
        log_event("LOGOUT", "SUCCESS", user_id=user_id,
                  detail="Session expired after inactivity")
    session.clear()
    flash(t("msg_session_expired"), "warning")
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
@role_required("FARMER", "EXTENSION_OFFICER", "ADMIN")
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        conn = get_db()
        user = conn.execute(
            "SELECT password_hash FROM app_user WHERE user_id = ?;",
            (session["user_id"],),
        ).fetchone()

        if not user or not check_password_hash(user["password_hash"], current_password):
            flash(t("msg_current_password_wrong"), "danger")
            return redirect(url_for("auth.change_password"))
        problem = _password_problem(new_password)
        if problem:
            flash(t(problem), "danger")
            return redirect(url_for("auth.change_password"))
        if new_password == current_password:
            flash(t("msg_password_reused"), "danger")
            return redirect(url_for("auth.change_password"))
        if new_password != confirm_password:
            flash(t("msg_password_mismatch"), "danger")
            return redirect(url_for("auth.change_password"))

        conn.execute(
            "UPDATE app_user SET password_hash=?, must_change_password=0, "
            "failed_attempts=0, locked_until=NULL WHERE user_id=?;",
            (generate_password_hash(new_password, method="pbkdf2:sha256"),
             session["user_id"]),
        )
        conn.commit()

        session["must_change_password"] = False
        rotate_csrf_token()
        log_event("PASSWORD_CHANGE", "SUCCESS", user_id=session["user_id"])
        flash(t("msg_password_changed"), "success")
        return redirect(url_for("main.index"))

    return render_template("change_password.html")


def _issue_reset_code(conn, user):
    """Create a 6-digit reset code for *user* and deliver it via Brevo.

    Falls back to the user's other contact channel if the primary fails.
    Returns True when the code was delivered.
    """
    conn.execute(
        "DELETE FROM password_reset_code WHERE user_id = ? OR expires_at <= ?;",
        (user["user_id"], utc_stamp()),
    )
    code = f"{secrets.randbelow(1000000):06d}"
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    public_id = secrets.token_urlsafe(16)
    expires_at = utc_stamp(utcnow() + timedelta(minutes=VERIFY_TTL_MINUTES))
    conn.execute(
        """
        INSERT INTO password_reset_code
            (public_id, user_id, code_hash, expires_at)
        VALUES (?, ?, ?, ?);
        """,
        (public_id, user["user_id"], code_hash, expires_at),
    )
    conn.commit()

    candidates = []
    if user["email"]:
        candidates.append(("email", user["email"]))
    if user["phone_number"]:
        candidates.append(("phone", user["phone_number"]))
    for kind, contact in candidates:
        try:
            brevo.send_verification_code(kind, contact, code)
            return public_id
        except brevo.BrevoError as exc:
            current_app.logger.error("Brevo reset delivery to %s failed: %s",
                                     contact, exc)
    return None


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Uniform, code-verified reset request (SEC-06).

    The same flash message is shown whether or not the identifier exists so
    the endpoint cannot be used to enumerate accounts.
    """
    from flask import current_app

    session.pop("reset_public_id", None)
    if request.method == "POST":
        if not allow_request("forgot", 5, 60):
            abort(429)
        identifier = bounded(request.form.get("identifier"), 120)
        conn = get_db()
        user = conn.execute(
            """
            SELECT user_id, email, phone_number FROM app_user
            WHERE username = ? OR email = ? OR phone_number = ?;
            """,
            (identifier, identifier, identifier),
        ).fetchone()

        if user and brevo.brevo_configured():
            public_id = _issue_reset_code(conn, user)
            if public_id:
                session["reset_public_id"] = public_id
                log_event("PASSWORD_RESET_REQUESTED", "SUCCESS",
                          user_id=user["user_id"], detail="Reset code sent")
            else:
                log_event("PASSWORD_RESET_REQUESTED", "FAILURE",
                          user_id=user["user_id"],
                          detail="Reset code could not be delivered")
        elif user:
            # Brevo not configured: legacy administrator-assisted flow.
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            expires = utc_stamp(utcnow() + timedelta(
                minutes=current_app.config["PASSWORD_RESET_TTL_MINUTES"]))
            conn.execute(
                "INSERT INTO password_reset_token "
                "(user_id, token_hash, expires_at) VALUES (?, ?, ?);",
                (user["user_id"], token_hash, expires),
            )
            conn.commit()
            contact = user["email"] or user["phone_number"]
            deliver_secret(contact, "Maize system password reset",
                           f"Reset token: {raw_token} (valid "
                           f"{current_app.config['PASSWORD_RESET_TTL_MINUTES']} min)")
            log_event("PASSWORD_RESET_REQUESTED", "SUCCESS",
                      user_id=user["user_id"], detail="Reset token issued")
        else:
            log_event("PASSWORD_RESET_REQUESTED", "SUCCESS",
                      user_id=None, detail="Reset request received")

        flash(t(GENERIC_RESET_RESPONSE), "info")
        return redirect(url_for("auth.forgot_password_verify"))

    return render_template("forgot_password.html")


def _reset_code_row(conn, public_id):
    return conn.execute(
        "SELECT * FROM password_reset_code WHERE public_id = ?;",
        (public_id,),
    ).fetchone()


@bp.route("/forgot-password/verify", methods=["GET", "POST"])
def forgot_password_verify():
    from flask import current_app

    conn = get_db()
    public_id = session.get("reset_public_id")
    row = _reset_code_row(conn, public_id) if public_id else None

    if row and row["expires_at"] <= utc_stamp():
        conn.execute(
            "DELETE FROM password_reset_code WHERE code_id = ?;",
            (row["code_id"],),
        )
        conn.commit()
        row = None
        session.pop("reset_public_id", None)

    if request.method == "POST":
        if not allow_request("reset_verify", 10, 60):
            abort(429)
        if not row:
            flash(t("msg_code_invalid"), "danger")
            return redirect(url_for("auth.forgot_password_verify"))
        code = bounded(request.form.get("code"), 6)
        if row["attempts"] >= VERIFY_MAX_ATTEMPTS:
            conn.execute(
                "DELETE FROM password_reset_code WHERE code_id = ?;",
                (row["code_id"],),
            )
            conn.commit()
            session.pop("reset_public_id", None)
            flash(t("msg_code_too_many"), "danger")
            return redirect(url_for("auth.forgot_password"))
        candidate = hashlib.sha256(code.encode("utf-8")).hexdigest() if code else ""
        if hmac.compare_digest(candidate, row["code_hash"]):
            user_id = row["user_id"]
            conn.execute(
                "DELETE FROM password_reset_code WHERE code_id = ?;",
                (row["code_id"],),
            )
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            expires = utc_stamp(utcnow() + timedelta(
                minutes=current_app.config["PASSWORD_RESET_TTL_MINUTES"]))
            conn.execute(
                "INSERT INTO password_reset_token "
                "(user_id, token_hash, expires_at) VALUES (?, ?, ?);",
                (user_id, token_hash, expires),
            )
            conn.commit()
            session.pop("reset_public_id", None)
            log_event("PASSWORD_RESET_REQUESTED", "SUCCESS",
                      user_id=user_id, detail="Reset code verified")
            return redirect(url_for("auth.reset_password", token=raw_token))
        conn.execute(
            "UPDATE password_reset_code SET attempts = attempts + 1 "
            "WHERE code_id = ?;",
            (row["code_id"],),
        )
        conn.commit()
        flash(t("msg_code_invalid"), "danger")
        return redirect(url_for("auth.forgot_password_verify"))

    return render_template("forgot_password_verify.html")


@bp.route("/forgot-password/verify/resend", methods=["POST"])
def forgot_password_verify_resend():
    from flask import current_app

    if not allow_request("reset_resend", 3, 300):
        abort(429)
    conn = get_db()
    public_id = session.get("reset_public_id")
    row = _reset_code_row(conn, public_id) if public_id else None
    if not row:
        flash(t("msg_code_invalid"), "danger")
        return redirect(url_for("auth.forgot_password"))
    user = conn.execute(
        "SELECT user_id, email, phone_number FROM app_user WHERE user_id = ?;",
        (row["user_id"],),
    ).fetchone()
    if not user:
        session.pop("reset_public_id", None)
        flash(t("msg_code_invalid"), "danger")
        return redirect(url_for("auth.forgot_password"))

    conn.execute(
        "DELETE FROM password_reset_code WHERE code_id = ?;",
        (row["code_id"],),
    )
    conn.commit()
    new_public_id = _issue_reset_code(conn, user)
    if new_public_id:
        session["reset_public_id"] = new_public_id
        flash(t("msg_code_resent"), "success")
    else:
        session.pop("reset_public_id", None)
        log_event("PASSWORD_RESET_REQUESTED", "FAILURE",
                  user_id=user["user_id"],
                  detail="Reset code resend could not be delivered")
        flash(t("msg_reset_unavailable"), "danger")
    return redirect(url_for("auth.forgot_password_verify"))


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Consume a single-use reset token (SEC-06)."""
    token_hash = hashlib.sha256(bounded(token, 200).encode("utf-8")).hexdigest()
    conn = get_db()
    record = conn.execute(
        """
        SELECT * FROM password_reset_token
        WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?;
        """,
        (token_hash, utc_stamp()),
    ).fetchone()
    if not record:
        flash(t("msg_reset_invalid"), "danger")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        new_password = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        problem = _password_problem(new_password)
        if problem:
            flash(t(problem), "danger")
            return redirect(request.path)
        if new_password != confirm:
            flash(t("msg_password_mismatch"), "danger")
            return redirect(request.path)

        conn.execute(
            "UPDATE app_user SET password_hash=?, must_change_password=0, "
            "failed_attempts=0, locked_until=NULL, account_status='ACTIVE' "
            "WHERE user_id=?;",
            (generate_password_hash(new_password, method="pbkdf2:sha256"),
             record["user_id"]),
        )
        conn.execute(
            "UPDATE password_reset_token SET used_at=? WHERE token_id=?;",
            (utc_stamp(), record["token_id"]),
        )
        conn.commit()
        log_event("PASSWORD_RESET", "SUCCESS", user_id=record["user_id"],
                  target_user_id=record["user_id"],
                  detail="Password reset completed via token")
        flash(t("msg_password_changed"), "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)


@bp.route("/activate", methods=["GET", "POST"])
def activate_officer():
    """Single-use, contact-verified officer activation (FR-03)."""
    from flask import current_app

    if request.method == "POST":
        if not allow_request("activate", 10, 60):
            abort(429)
        code = bounded(request.form.get("code"), 200)
        contact = bounded(request.form.get("contact"), 120)
        username = bounded(request.form.get("username"), 32)
        full_name = bounded(request.form.get("full_name"), 120)
        password = request.form.get("password") or ""

        if not all([code, contact, username, full_name, password]):
            flash(t("msg_fields_required"), "danger")
            return redirect(url_for("auth.activate_officer"))
        if not valid_full_name(full_name) or not valid_username(username):
            flash(t("msg_invalid_name"), "danger")
            return redirect(url_for("auth.activate_officer"))
        problem = _password_problem(password)
        if problem:
            flash(t(problem), "danger")
            return redirect(url_for("auth.activate_officer"))

        token_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        conn = get_db()
        invite = conn.execute(
            """
            SELECT * FROM officer_invitation
            WHERE token_hash=? AND status='PENDING' AND expires_at > ?;
            """,
            (token_hash, utc_stamp()),
        ).fetchone()

        if not invite or invite["invitee_contact"].strip().lower() != contact.lower():
            if invite:
                log_event("REGISTER_OFFICER", "FAILURE",
                          detail="Activation contact did not match invitation")
            flash(t("msg_activation_invalid"), "danger")
            return redirect(url_for("auth.activate_officer"))

        contact_kind, contact_value = parse_contact(contact)
        email = contact_value if contact_kind == "email" else None
        phone = contact_value if contact_kind == "phone" else None
        try:
            cursor = conn.execute(
                """
                INSERT INTO app_user
                    (full_name, username, email, phone_number, password_hash,
                     role_id, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (full_name, username, email, phone,
                 generate_password_hash(password, method="pbkdf2:sha256"),
                 invite["role_id"], invite["issued_by"]),
            )
            new_user_id = cursor.lastrowid
            conn.execute(
                "UPDATE officer_invitation SET status='USED', used_by=?, used_at=? "
                "WHERE invitation_id=?;",
                (new_user_id, utc_stamp(), invite["invitation_id"]),
            )
            conn.commit()
            log_event("REGISTER_OFFICER", "SUCCESS", user_id=new_user_id,
                      detail=f"Account activated via invitation (role_id={invite['role_id']})")
            flash(t("msg_activation_success"), "success")
            return redirect(url_for("auth.login"))
        except sqlite3.IntegrityError:
            conn.rollback()
            log_event("REGISTER_OFFICER", "FAILURE",
                      detail="Username/contact already in use")
            flash(t("msg_duplicate"), "danger")

    return render_template("activate.html")
