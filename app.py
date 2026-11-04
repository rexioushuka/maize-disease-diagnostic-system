"""Maize Crop Disease Diagnostic System.

Flask application implementing the functional and non-functional
requirements described in the project report:

* farmer self-registration and role-based sign-in
* invitation-only extension officer accounts
* MobileNetV2 leaf diagnosis with bilingual IPM advisory
* extension officer surveillance dashboard, filters and anonymised CSV
* administrator console for users, invitations and audit log
* append-only audit trail, CSRF protection, idle session timeout and
  server-side input/image validation

The model is loaded once at start-up and kept in memory.
"""

import csv
import hashlib
import io
import os
import secrets
import socket
import sqlite3
import time
from datetime import datetime, timedelta
from functools import wraps
from threading import Lock
from urllib.parse import urlparse

import numpy as np
import tensorflow as tf
from PIL import Image, UnidentifiedImageError
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    Response,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from i18n import LANGUAGES, translate

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "maize_system.db"))
MODEL_PATH = os.environ.get(
    "MODEL_PATH", os.path.join(BASE_DIR, "models", "mobilenetv2_maize.h5")
)
UPLOAD_FOLDER = os.environ.get(
    "UPLOAD_FOLDER", os.path.join(BASE_DIR, "instance", "uploads")
)
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024          # FR-07
CONFIDENCE_THRESHOLD = 0.70                 # FR-11
SESSION_IDLE_MINUTES = int(os.environ.get("SESSION_IDLE_MINUTES", "30"))  # FR-05

MODEL_VERSION = "MobileNetV2-v1.0"
CLASS_NAMES = ["Downy_Mildew", "Healthy", "MLN", "MSV"]

os.makedirs(INSTANCE_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def _load_secret_key():
    """Use SECRET_KEY from the environment, otherwise persist a local key."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    key_path = os.path.join(INSTANCE_DIR, "secret_key")
    if os.path.exists(key_path):
        with open(key_path, "rb") as fh:
            return fh.read()
    key = secrets.token_bytes(32)
    with open(key_path, "wb") as fh:
        fh.write(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_load_secret_key(),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=SESSION_IDLE_MINUTES),
    JSON_SORT_KEYS=False,
)

# Load the trained model once (FR-09).  A missing model must fail loudly.
print("[+] Loading trained MobileNetV2 model into memory...")
model = tf.keras.models.load_model(MODEL_PATH)
MODEL_LOADED = True

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def t(key, **kwargs):
    """Translate a string using the language selected in the session."""
    return translate(session.get("lang", "en"), key, **kwargs)


def log_event(event_code, outcome, user_id=None, target_user_id=None,
              diagnosis_id=None, detail=""):
    """Append one audit event (FR-19, FR-21). Never raises to the user."""
    try:
        try:
            ip_addr = request.remote_addr
            req_path = request.path
        except RuntimeError:
            ip_addr = None
            req_path = None

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT event_type_id FROM event_type WHERE event_code = ?;",
            (event_code,),
        )
        row = cursor.fetchone()
        if row:
            cursor.execute(
                """
                INSERT INTO system_log
                    (user_id, event_type_id, outcome, source_ip, request_path,
                     target_user_id, diagnosis_id, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (user_id, row["event_type_id"], outcome, ip_addr, req_path,
                 target_user_id, diagnosis_id, (detail or "")[:500]),
            )
            conn.commit()
        conn.close()
    except Exception as exc:  # pragma: no cover - logging must not break requests
        app.logger.error("Failed to write audit log: %s", exc)


# Additional controlled-vocabulary entries used by the completed system.
EXTRA_EVENT_TYPES = [
    (17, "PASSWORD_CHANGE", "AUTHENTICATION", "User changed own password"),
    (18, "PASSWORD_RESET", "ADMINISTRATION", "Administrator reset a password"),
]


def ensure_reference_data():
    """Add audit event types that may be missing from an older database."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT OR IGNORE INTO event_type "
            "(event_type_id, event_code, category, description) VALUES (?, ?, ?, ?);",
            EXTRA_EVENT_TYPES,
        )
        conn.commit()
        conn.close()
    except sqlite3.Error as exc:
        app.logger.warning("Could not verify reference data: %s", exc)


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------
CSRF_SESSION_KEY = "_csrf_token"
_rate_lock = Lock()
_rate_buckets = {}


def csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _safe_redirect_target(target):
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


def check_rate_limit(key, limit, window_seconds):
    """Simple in-process rate limiter (NFR-04 abuse control)."""
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets.setdefault(key, [])
        bucket[:] = [stamp for stamp in bucket if now - stamp < window_seconds]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def role_required(*allowed_roles):
    """Server-side role check on every protected route (FR-06)."""
    def decorator(func):
        @wraps(func)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                log_event(
                    "UNAUTHORIZED_ATTEMPT",
                    "DENIED",
                    detail=f"Unauthenticated access to {request.path}",
                )
                abort(401, description=t("error.401"))
            if session.get("role_name") not in allowed_roles:
                log_event(
                    "UNAUTHORIZED_ATTEMPT",
                    "DENIED",
                    user_id=session.get("user_id"),
                    detail=f"Access denied to {request.path}",
                )
                abort(403)
            return func(*args, **kwargs)
        return decorated_function
    return decorator


@app.before_request
def enforce_session_and_csrf():
    if request.endpoint == "static":
        return None

    # Idle-session timeout (FR-05 / NFR-06).
    if "user_id" in session:
        now = datetime.utcnow()
        last = session.get("_last_activity")
        if last:
            try:
                last_dt = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                last_dt = None
            if last_dt and now - last_dt > timedelta(minutes=SESSION_IDLE_MINUTES):
                user_id = session.get("user_id")
                session.clear()
                log_event("LOGOUT", "SUCCESS", user_id=user_id,
                          detail="Session expired after inactivity")
                flash(t("msg_session_expired"), "warning")
                return redirect(url_for("login"))
        session["_last_activity"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # A deactivated or removed account must lose access immediately (FR-17).
        conn = get_db()
        account = conn.execute(
            "SELECT account_status, must_change_password FROM app_user "
            "WHERE user_id = ?;",
            (session["user_id"],),
        ).fetchone()
        conn.close()
        if not account or account["account_status"] != "ACTIVE":
            user_id = session.get("user_id")
            session.clear()
            log_event("LOGOUT", "SUCCESS", user_id=user_id,
                      detail="Session ended because the account is not active")
            flash(t("msg_account_disabled"), "danger")
            return redirect(url_for("login"))

        # Reflect an administrator reset even in an already-open session (FR-17).
        session["must_change_password"] = bool(account["must_change_password"])

        # A temporary password must be changed before anything else (FR-17).
        if session.get("must_change_password") and request.endpoint not in {
            "change_password", "logout", "set_language", "static"
        }:
            flash(t("msg_must_change"), "warning")
            return redirect(url_for("change_password"))

    # CSRF protection for state-changing requests (NFR-06).
    if request.method == "POST":
        token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
        expected = session.get(CSRF_SESSION_KEY)
        if not token or not expected or not secrets.compare_digest(str(token), str(expected)):
            log_event("SYSTEM_ERROR", "FAILURE", user_id=session.get("user_id"),
                      detail="CSRF token missing or invalid")
            abort(400, description=t("msg_csrf"))
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; media-src 'self' blob:; "
        "font-src 'self' https://cdn.jsdelivr.net; connect-src 'self'",
    )
    response.headers.setdefault(
        "Permissions-Policy", "camera=(self), microphone=(), geolocation=()"
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.context_processor
def inject_globals():
    return {
        "t": t,
        "lang": session.get("lang", "en"),
        "languages": LANGUAGES,
        "csrf_token": csrf_token,
        "current_year": datetime.utcnow().year,
    }


ROLE_LABEL_KEYS = {
    "FARMER": "status.role_farmer",
    "EXTENSION_OFFICER": "status.role_officer",
    "ADMIN": "status.role_admin",
}

CLASS_LABEL_CODES = {
    "Downy_Mildew": "Downy_Mildew",
    "Downy Mildew": "Downy_Mildew",
    "Healthy": "Healthy",
    "MLN": "MLN",
    "Maize Lethal Necrosis (MLN)": "MLN",
    "MSV": "MSV",
    "Maize Streak Virus (MSV)": "MSV",
}


def role_label(value):
    key = ROLE_LABEL_KEYS.get(str(value or "").upper())
    return t(key) if key else str(value or "").replace("_", " ").title()


def status_label(value):
    if not value:
        return ""
    key = f"status.{str(value).lower()}"
    label = translate(session.get("lang", "en"), key)
    return str(value).replace("_", " ").title() if label == key else label


def class_label(value):
    code = CLASS_LABEL_CODES.get(str(value or ""), str(value or ""))
    key = f"class.{code}"
    label = translate(session.get("lang", "en"), key)
    return str(value) if label == key else label


app.jinja_env.filters["role_label"] = role_label
app.jinja_env.filters["status_label"] = status_label
app.jinja_env.filters["class_label"] = class_label


# ---------------------------------------------------------------------------
# Image diagnosis service
# ---------------------------------------------------------------------------
def validate_and_open_image(file_storage):
    """Validate an uploaded image by content and return raw bytes + PIL image."""
    if file_storage is None or not file_storage.filename:
        raise ValueError(t("msg_no_file"))

    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(t("msg_invalid_file_type"))

    raw = file_storage.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise ValueError(t("msg_no_file"))
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(t("msg_file_too_large"))

    is_jpeg = raw.startswith(b"\xff\xd8\xff")
    is_png = raw.startswith(b"\x89PNG\r\n\x1a\n")
    if not (is_jpeg or is_png):
        raise ValueError(t("msg_invalid_file_type"))

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValueError(t("msg_image_corrupt"))

    return raw, image


def _valid_section_id(raw_section_id):
    if raw_section_id in (None, ""):
        return None
    try:
        section_id = int(raw_section_id)
    except (TypeError, ValueError):
        raise ValueError(t("msg_invalid_section"))
    conn = get_db()
    row = conn.execute(
        "SELECT section_id FROM village_section WHERE section_id = ?;",
        (section_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(t("msg_invalid_section"))
    return section_id


def run_diagnosis(file_storage, section_id, user_id, language_code):
    """Validate, classify, persist and return one diagnosis (FR-07..FR-12)."""
    start_time = time.time()
    raw, image = validate_and_open_image(file_storage)
    section_id = _valid_section_id(section_id)

    img_sha256 = hashlib.sha256(raw).hexdigest()

    # Persist a compressed evidence copy outside the web root.
    stored_image = image.copy()
    stored_image.thumbnail((1024, 1024))
    file_path = os.path.join(UPLOAD_FOLDER, f"{img_sha256}.jpg")
    stored_image.save(file_path, "JPEG", quality=88)

    model_input = image.resize((224, 224))
    img_array = np.asarray(model_input, dtype=np.float32) / 255.0
    img_tensor = np.expand_dims(img_array, axis=0)

    infer_start = time.time()
    predictions = model.predict(img_tensor, verbose=0)[0]
    infer_ms = int((time.time() - infer_start) * 1000)

    predicted_class_id = int(np.argmax(predictions))
    top_confidence = float(predictions[predicted_class_id])
    total_ms = int((time.time() - start_time) * 1000)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO diagnosis
            (user_id, class_id, section_id, image_sha256, image_path,
             model_version, inference_ms, total_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (user_id, predicted_class_id, section_id, img_sha256, file_path,
         MODEL_VERSION, infer_ms, total_ms),
    )
    diagnosis_id = cursor.lastrowid

    for cls_id, probability in enumerate(predictions):
        cursor.execute(
            """
            INSERT INTO diagnosis_probability
                (diagnosis_id, class_id, probability)
            VALUES (?, ?, ?);
            """,
            (diagnosis_id, cls_id, float(probability)),
        )
    conn.commit()

    advisory = cursor.execute(
        """
        SELECT a.*, c.display_name
        FROM advisory a JOIN disease_class c ON a.class_id = c.class_id
        WHERE a.class_id = ? AND a.language_code = ?;
        """,
        (predicted_class_id, language_code),
    ).fetchone()
    if advisory is None:
        advisory = cursor.execute(
            """
            SELECT a.*, c.display_name
            FROM advisory a JOIN disease_class c ON a.class_id = c.class_id
            WHERE a.class_id = ? AND a.language_code = 'en';
            """,
            (predicted_class_id,),
        ).fetchone()

    class_rows = cursor.execute(
        "SELECT class_id, display_name FROM disease_class ORDER BY class_id;"
    ).fetchall()
    display_names = {row["class_id"]: row["display_name"] for row in class_rows}
    conn.close()

    probabilities = [
        {
            "class_name": CLASS_NAMES[idx],
            "display_name": display_names.get(idx, CLASS_NAMES[idx]),
            "probability": round(float(probability) * 100, 2),
        }
        for idx, probability in enumerate(predictions)
    ]

    log_event(
        "MODEL_INFERENCE",
        "SUCCESS",
        user_id=user_id,
        diagnosis_id=diagnosis_id,
        detail=f"Class: {CLASS_NAMES[predicted_class_id]}, Conf: {top_confidence:.4f}",
    )

    return {
        "diagnosis_id": diagnosis_id,
        "class_name": CLASS_NAMES[predicted_class_id],
        "display_name": display_names.get(predicted_class_id,
                                          CLASS_NAMES[predicted_class_id]),
        "confidence": round(top_confidence * 100, 2),
        "low_confidence": top_confidence < CONFIDENCE_THRESHOLD,
        "advisory": dict(advisory) if advisory else None,
        "probabilities": probabilities,
        "inference_ms": infer_ms,
        "total_ms": total_ms,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# Authentication routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    if "user_id" in session:
        role = session.get("role_name")
        if role == "FARMER":
            return redirect(url_for("farmer_dashboard"))
        if role == "EXTENSION_OFFICER":
            return redirect(url_for("officer_dashboard"))
        if role == "ADMIN":
            return redirect(url_for("admin_dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    """Public farmer registration only (FR-01, FR-02)."""
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        username = (request.form.get("username") or "").strip()
        contact = (request.form.get("contact") or "").strip()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password")
        section_id = request.form.get("section_id")

        if not all([full_name, username, contact, password]):
            flash(t("msg_fields_required"), "danger")
            return redirect(url_for("register"))
        if len(password) < 8:
            flash(t("msg_password_short"), "danger")
            return redirect(url_for("register"))
        if confirm_password is not None and confirm_password != password:
            flash(t("msg_password_mismatch"), "danger")
            return redirect(url_for("register"))

        try:
            section_id = _valid_section_id(section_id)
        except ValueError:
            flash(t("msg_invalid_section"), "danger")
            return redirect(url_for("register"))
        if section_id is None:
            flash(t("msg_invalid_section"), "danger")
            return redirect(url_for("register"))

        email = contact if "@" in contact else None
        phone = contact if "@" not in contact else None
        password_hash = generate_password_hash(password, method="pbkdf2:sha256")

        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO app_user
                    (full_name, username, email, phone_number, password_hash,
                     role_id, section_id)
                VALUES (?, ?, ?, ?, ?, 1, ?);
                """,
                (full_name, username, email, phone, password_hash, section_id),
            )
            conn.commit()
            new_user_id = cursor.lastrowid
            log_event("REGISTER_FARMER", "SUCCESS", user_id=new_user_id,
                      detail="Farmer registered successfully")
            flash(t("msg_registered"), "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            log_event("REGISTER_FARMER", "FAILURE",
                      detail=f"Duplicate registration attempt for {username}")
            flash(t("msg_duplicate"), "danger")
        finally:
            conn.close()

    conn = get_db()
    sections = conn.execute("SELECT * FROM village_section ORDER BY section_name;").fetchall()
    conn.close()
    return render_template("register.html", sections=sections)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Sign in with username, email or phone (FR-04, FR-05)."""
    if request.method == "POST":
        if not check_rate_limit(f"login:{request.remote_addr}", 10, 60):
            log_event("LOGIN_FAILURE", "DENIED",
                      detail="Login rate limit exceeded")
            abort(429)

        login_input = (request.form.get("login_input") or "").strip()
        password = request.form.get("password") or ""

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT u.*, r.role_name FROM app_user u
            JOIN role r ON u.role_id = r.role_id
            WHERE u.username = ? OR u.email = ? OR u.phone_number = ?;
            """,
            (login_input, login_input, login_input),
        )
        user = cursor.fetchone()

        if user is None or user["account_status"] == "DEACTIVATED":
            log_event("LOGIN_FAILURE", "FAILURE",
                      detail="Failed login attempt")
            conn.close()
            flash(t("msg_invalid_credentials"), "danger")
            return render_template("login.html")

        # Expire an old lock automatically.
        if user["account_status"] == "LOCKED":
            locked_until = user["locked_until"]
            if locked_until:
                try:
                    still_locked = datetime.strptime(
                        locked_until, "%Y-%m-%dT%H:%M:%SZ"
                    ) > datetime.utcnow()
                except ValueError:
                    still_locked = False
            else:
                still_locked = False
            if still_locked:
                log_event("LOGIN_FAILURE", "FAILURE", user_id=user["user_id"],
                          detail="Account locked")
                conn.close()
                flash(t("msg_account_locked"), "danger")
                return render_template("login.html")
            cursor.execute(
                "UPDATE app_user SET account_status = 'ACTIVE', failed_attempts = 0, "
                "locked_until = NULL WHERE user_id = ?;",
                (user["user_id"],),
            )
            conn.commit()

        if check_password_hash(user["password_hash"], password):
            cursor.execute(
                "UPDATE app_user SET failed_attempts = 0, locked_until = NULL, "
                "account_status = 'ACTIVE', last_login_at = ? WHERE user_id = ?;",
                (datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), user["user_id"]),
            )
            conn.commit()

            selected_lang = session.get("lang", "en")
            user_id = user["user_id"]
            session.clear()  # prevent session fixation
            session.permanent = True
            session["user_id"] = user_id
            session["username"] = user["username"]
            session["full_name"] = user["full_name"]
            session["role_name"] = user["role_name"]
            session["must_change_password"] = bool(user["must_change_password"])
            session["lang"] = selected_lang
            session["_last_activity"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

            log_event("LOGIN_SUCCESS", "SUCCESS", user_id=user_id)
            conn.close()
            if session["must_change_password"]:
                flash(t("msg_must_change"), "warning")
                return redirect(url_for("change_password"))
            return redirect(url_for("index"))

        # Wrong password: increment and lock after five failures.
        failed = user["failed_attempts"] + 1
        status = user["account_status"]
        locked_until = None
        if failed >= 5:
            status = "LOCKED"
            locked_until = (
                datetime.utcnow() + timedelta(minutes=15)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            log_event("ACCOUNT_LOCKED", "FAILURE", user_id=user["user_id"],
                      detail="5 consecutive failed logins")

        cursor.execute(
            "UPDATE app_user SET failed_attempts = ?, locked_until = ?, "
            "account_status = ? WHERE user_id = ?;",
            (failed, locked_until, status, user["user_id"]),
        )
        conn.commit()
        log_event("LOGIN_FAILURE", "FAILURE", user_id=user["user_id"],
                  detail="Failed login attempt")
        conn.close()
        flash(t("msg_invalid_credentials"), "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id:
        log_event("LOGOUT", "SUCCESS", user_id=user_id)
    session.clear()
    flash(t("msg_logged_out"), "info")
    return redirect(url_for("login"))


@app.route("/change-password", methods=["GET", "POST"])
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
            conn.close()
            flash(t("msg_current_password_wrong"), "danger")
            return redirect(url_for("change_password"))
        if len(new_password) < 8:
            conn.close()
            flash(t("msg_password_short"), "danger")
            return redirect(url_for("change_password"))
        if new_password != confirm_password:
            conn.close()
            flash(t("msg_password_mismatch"), "danger")
            return redirect(url_for("change_password"))

        conn.execute(
            "UPDATE app_user SET password_hash = ?, must_change_password = 0, "
            "failed_attempts = 0, locked_until = NULL WHERE user_id = ?;",
            (generate_password_hash(new_password, method="pbkdf2:sha256"),
             session["user_id"]),
        )
        conn.commit()
        conn.close()

        session["must_change_password"] = False
        log_event("PASSWORD_CHANGE", "SUCCESS", user_id=session["user_id"])
        flash(t("msg_password_changed"), "success")
        return redirect(url_for("index"))

    return render_template("change_password.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        identifier = (request.form.get("identifier") or "").strip()
        conn = get_db()
        user = conn.execute(
            """
            SELECT user_id, username FROM app_user
            WHERE username = ? OR email = ? OR phone_number = ?;
            """,
            (identifier, identifier, identifier),
        ).fetchone()
        conn.close()

        if user:
            log_event("PASSWORD_RESET", "SUCCESS", user_id=user["user_id"],
                      detail="Password recovery requested; pending admin action")
            flash(t("msg_recovery_requested"), "info")
        else:
            flash(t("msg_account_not_found"), "danger")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


# ---------------------------------------------------------------------------
# Farmer / diagnosis routes
# ---------------------------------------------------------------------------
@app.route("/farmer/dashboard", methods=["GET", "POST"])
@role_required("FARMER", "EXTENSION_OFFICER", "ADMIN")
def farmer_dashboard():
    lang = session.get("lang", "en")
    conn = get_db()

    if request.method == "POST":
        if not check_rate_limit(
            f"diagnose:{session['user_id']}:{request.remote_addr}", 20, 60
        ):
            conn.close()
            log_event("UNAUTHORIZED_ATTEMPT", "DENIED",
                      user_id=session["user_id"], detail="Diagnosis rate limit exceeded")
            abort(429)

        file = request.files.get("file")
        section_id = request.form.get("section_id")

        try:
            result = run_diagnosis(file, section_id, session["user_id"], lang)
        except ValueError as exc:
            conn.close()
            flash(str(exc), "danger")
            return redirect(url_for("farmer_dashboard"))
        except Exception as exc:  # pragma: no cover - defensive
            conn.close()
            app.logger.exception("Diagnosis failed")
            log_event("SYSTEM_ERROR", "FAILURE", user_id=session.get("user_id"),
                      detail=str(exc))
            flash(t("msg_diagnosis_error"), "danger")
            return redirect(url_for("farmer_dashboard"))

        sections = conn.execute(
            "SELECT * FROM village_section ORDER BY section_name;"
        ).fetchall()
        history = _user_history(conn, session["user_id"])
        user_section_id = _user_section_id(conn, session["user_id"])
        conn.close()
        return render_template(
            "farmer.html", sections=sections, history=history, result=result,
            user_section_id=user_section_id,
        )

    sections = conn.execute(
        "SELECT * FROM village_section ORDER BY section_name;"
    ).fetchall()
    history = _user_history(conn, session["user_id"])
    user_section_id = _user_section_id(conn, session["user_id"])
    conn.close()
    return render_template(
        "farmer.html", sections=sections, history=history,
        user_section_id=user_section_id,
    )


def _user_section_id(conn, user_id):
    row = conn.execute(
        "SELECT section_id FROM app_user WHERE user_id = ?;", (user_id,)
    ).fetchone()
    return row["section_id"] if row else None


def _user_history(conn, user_id):
    return conn.execute(
        """
        SELECT d.*, c.class_name, c.display_name, p.probability AS confidence
        FROM diagnosis d
        JOIN disease_class c ON d.class_id = c.class_id
        JOIN diagnosis_probability p
          ON d.diagnosis_id = p.diagnosis_id AND d.class_id = p.class_id
        WHERE d.user_id = ?
        ORDER BY d.created_at DESC;
        """,
        (user_id,),
    ).fetchall()


@app.route("/predict", methods=["POST"])
@role_required("FARMER", "EXTENSION_OFFICER", "ADMIN")
def predict_api():
    """JSON diagnosis endpoint used by the original prototype client."""
    if not check_rate_limit(
        f"diagnose:{session['user_id']}:{request.remote_addr}", 20, 60
    ):
        return jsonify(success=False, error=t("msg_rate_limited")), 429

    file = request.files.get("image") or request.files.get("file")
    try:
        result = run_diagnosis(
            file, request.form.get("section_id"), session["user_id"],
            session.get("lang", "en"),
        )
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except Exception as exc:  # pragma: no cover - defensive
        app.logger.exception("Prediction API failed")
        log_event("SYSTEM_ERROR", "FAILURE", user_id=session.get("user_id"),
                  detail=str(exc))
        return jsonify(success=False, error=t("msg_diagnosis_error")), 500

    return jsonify(
        success=True,
        diagnosis={
            "id": result["diagnosis_id"],
            "disease": class_label(result["class_name"]),
            "class_name": result["class_name"],
            "status": ("Healthy" if result["class_name"] == "Healthy"
                       else "Disease Detected"),
            "confidence_score": f"{result['confidence']}%",
            "confidence": result["confidence"],
            "low_confidence": result["low_confidence"],
            "description": result["advisory"]["description"] if result["advisory"] else "",
            "recommended_action": result["advisory"]["action_text"] if result["advisory"] else "",
            "source": result["advisory"]["source_ref"] if result["advisory"] else "",
            "probabilities": result["probabilities"],
            "total_ms": result["total_ms"],
        },
    )


# ---------------------------------------------------------------------------
# Extension officer dashboard and reporting (FR-14, FR-15)
# ---------------------------------------------------------------------------
def _parse_diag_filters(args):
    """Return SQL where clause, parameters and normalised filter values."""
    where = ["1=1"]
    params = []
    filters = {
        "date_from": (args.get("date_from") or "").strip(),
        "date_to": (args.get("date_to") or "").strip(),
        "class_id": (args.get("class_id") or "").strip(),
        "section_id": (args.get("section_id") or "").strip(),
    }

    for key, column in (("date_from", ">="), ("date_to", "<=")):
        value = filters[key]
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                filters[key] = ""
                continue
            where.append(f"date(d.created_at) {column} date(?)")
            params.append(value)

    if filters["class_id"].isdigit():
        where.append("d.class_id = ?")
        params.append(int(filters["class_id"]))
    else:
        filters["class_id"] = ""

    if filters["section_id"].isdigit():
        where.append("d.section_id = ?")
        params.append(int(filters["section_id"]))
    else:
        filters["section_id"] = ""

    return " AND ".join(where), params, filters


@app.route("/officer/dashboard")
@role_required("EXTENSION_OFFICER", "ADMIN")
def officer_dashboard():
    where, params, filters = _parse_diag_filters(request.args)
    conn = get_db()
    cursor = conn.cursor()

    total_scans = cursor.execute(
        f"SELECT COUNT(*) FROM diagnosis d WHERE {where};", params
    ).fetchone()[0]
    outbreaks = cursor.execute(
        f"""SELECT COUNT(*) FROM diagnosis d
            WHERE {where} AND d.class_id IN
                (SELECT class_id FROM disease_class WHERE is_disease = 1);""",
        params,
    ).fetchone()[0]
    healthy_cases = cursor.execute(
        f"""SELECT COUNT(*) FROM diagnosis d
            WHERE {where} AND d.class_id IN
                (SELECT class_id FROM disease_class WHERE is_disease = 0);""",
        params,
    ).fetchone()[0]

    class_counts = cursor.execute(
        f"""
        SELECT c.class_id, c.class_name, c.display_name, COUNT(d.diagnosis_id) AS count
        FROM disease_class c
        LEFT JOIN diagnosis d ON c.class_id = d.class_id AND ({where})
        GROUP BY c.class_id
        ORDER BY c.class_id;
        """,
        params,
    ).fetchall()

    weekly_trend = cursor.execute(
        f"""
        SELECT strftime('%Y-%W', d.created_at) AS week, COUNT(*) AS count
        FROM diagnosis d
        WHERE {where}
        GROUP BY week
        ORDER BY week DESC
        LIMIT 8;
        """,
        params,
    ).fetchall()
    weekly_trend = list(reversed(weekly_trend))

    by_section = cursor.execute(
        f"""
        SELECT COALESCE(s.section_name, 'Unspecified') AS section_name,
               COUNT(*) AS count,
               SUM(CASE WHEN d.class_id IN
                   (SELECT class_id FROM disease_class WHERE is_disease = 1)
                   THEN 1 ELSE 0 END) AS disease_count
        FROM diagnosis d
        LEFT JOIN village_section s ON d.section_id = s.section_id
        WHERE {where}
        GROUP BY s.section_id
        ORDER BY count DESC;
        """,
        params,
    ).fetchall()

    recent_logs = cursor.execute(
        f"""
        SELECT d.diagnosis_id, d.created_at, c.class_name, c.display_name,
               p.probability AS confidence, s.section_name
        FROM diagnosis d
        JOIN disease_class c ON d.class_id = c.class_id
        JOIN diagnosis_probability p
          ON d.diagnosis_id = p.diagnosis_id AND d.class_id = p.class_id
        LEFT JOIN village_section s ON d.section_id = s.section_id
        WHERE {where}
        ORDER BY d.created_at DESC
        LIMIT 25;
        """,
        params,
    ).fetchall()

    sections = cursor.execute(
        "SELECT * FROM village_section ORDER BY section_name;"
    ).fetchall()
    classes = cursor.execute(
        "SELECT * FROM disease_class ORDER BY class_id;"
    ).fetchall()

    log_event("DASHBOARD_VIEW", "SUCCESS", user_id=session["user_id"],
              detail=f"Filters: {filters}")
    conn.close()

    return render_template(
        "officer.html",
        total_scans=total_scans,
        outbreaks=outbreaks,
        healthy_cases=healthy_cases,
        class_counts=class_counts,
        weekly_trend=weekly_trend,
        by_section=by_section,
        recent_logs=recent_logs,
        sections=sections,
        classes=classes,
        filters=filters,
    )


@app.route("/officer/export")
@role_required("EXTENSION_OFFICER", "ADMIN")
def export_report():
    """Anonymised CSV export of the filtered diagnosis summary (FR-15)."""
    where, params, filters = _parse_diag_filters(request.args)
    conn = get_db()
    rows = conn.execute(
        f"""
        SELECT d.diagnosis_id, d.created_at, c.class_name, c.display_name,
               p.probability AS confidence, s.section_name,
               d.model_version
        FROM diagnosis d
        JOIN disease_class c ON d.class_id = c.class_id
        JOIN diagnosis_probability p
          ON d.diagnosis_id = p.diagnosis_id AND d.class_id = p.class_id
        LEFT JOIN village_section s ON d.section_id = s.section_id
        WHERE {where}
        ORDER BY d.created_at DESC;
        """,
        params,
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        t("export.id"), t("export.datetime"), t("export.class"),
        t("export.confidence"), t("export.section"), t("export.model"),
    ])
    for row in rows:
        writer.writerow([
            row["diagnosis_id"],
            row["created_at"],
            class_label(row["class_name"]),
            f"{row['confidence'] * 100:.2f}%",
            row["section_name"] or t("common.unspecified"),
            row["model_version"],
        ])

    log_event("REPORT_EXPORT", "SUCCESS", user_id=session["user_id"],
              detail=f"{len(rows)} rows; filters={filters}")
    filename = f"maize_diagnoses_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Administrator console (FR-16 to FR-21)
# ---------------------------------------------------------------------------
def _active_admin_count(conn, exclude_user_id=None):
    sql = """
        SELECT COUNT(*) FROM app_user u JOIN role r ON u.role_id = r.role_id
        WHERE r.role_name = 'ADMIN' AND u.account_status = 'ACTIVE'
    """
    params = []
    if exclude_user_id is not None:
        sql += " AND u.user_id != ?"
        params.append(exclude_user_id)
    return conn.execute(sql, params).fetchone()[0]


@app.route("/admin/dashboard")
@role_required("ADMIN")
def admin_dashboard():
    q = (request.args.get("q") or "").strip()
    role_filter = (request.args.get("role") or "").strip()
    status_filter = (request.args.get("status") or "").strip()

    log_user = (request.args.get("log_user") or "").strip()
    log_event_filter = (request.args.get("log_event") or "").strip()
    log_outcome = (request.args.get("log_outcome") or "").strip()
    log_from = (request.args.get("log_from") or "").strip()
    log_to = (request.args.get("log_to") or "").strip()

    conn = get_db()
    cursor = conn.cursor()

    # User filters (FR-17).
    user_where = ["1=1"]
    user_params = []
    if q:
        like = f"%{q}%"
        user_where.append(
            "(u.full_name LIKE ? OR u.username LIKE ? OR u.email LIKE ? OR u.phone_number LIKE ?)"
        )
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
        WHERE {' AND '.join(user_where)}
        ORDER BY u.user_id;
        """,
        user_params,
    ).fetchall()

    now_utc = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    invitations = cursor.execute(
        """
        SELECT i.*, issuer.username AS issued_by_name,
               CASE WHEN i.status = 'PENDING' AND i.expires_at <= ?
                    THEN 'EXPIRED' ELSE i.status END AS display_status
        FROM officer_invitation i
        LEFT JOIN app_user issuer ON i.issued_by = issuer.user_id
        ORDER BY i.issued_at DESC
        LIMIT 50;
        """,
        (now_utc,),
    ).fetchall()

    # Audit log filters (FR-20).
    log_where = ["1=1"]
    log_params = []
    if log_from:
        try:
            datetime.strptime(log_from, "%Y-%m-%d")
            log_where.append("date(l.occurred_at) >= date(?)")
            log_params.append(log_from)
        except ValueError:
            log_from = ""
    if log_to:
        try:
            datetime.strptime(log_to, "%Y-%m-%d")
            log_where.append("date(l.occurred_at) <= date(?)")
            log_params.append(log_to)
        except ValueError:
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
        ORDER BY l.occurred_at DESC, l.log_id DESC
        LIMIT 200;
        """,
        log_params,
    ).fetchall()

    event_types = cursor.execute(
        "SELECT * FROM event_type ORDER BY event_code;"
    ).fetchall()

    total_users = cursor.execute("SELECT COUNT(*) FROM app_user;").fetchone()[0]
    active_users = cursor.execute(
        "SELECT COUNT(*) FROM app_user WHERE account_status = 'ACTIVE';"
    ).fetchone()[0]
    total_diagnoses = cursor.execute("SELECT COUNT(*) FROM diagnosis;").fetchone()[0]
    pending_invites = cursor.execute(
        "SELECT COUNT(*) FROM officer_invitation "
        "WHERE status = 'PENDING' AND expires_at > ?;",
        (now_utc,),
    ).fetchone()[0]

    log_event("LOG_VIEW", "SUCCESS", user_id=session["user_id"])
    conn.close()

    return render_template(
        "admin.html",
        total_users=total_users,
        active_users=active_users,
        total_diagnoses=total_diagnoses,
        pending_invites=pending_invites,
        users=users,
        invitations=invitations,
        logs=logs,
        event_types=event_types,
        q=q,
        role_filter=role_filter,
        status_filter=status_filter,
        log_user=log_user,
        log_event=log_event_filter,
        log_outcome=log_outcome,
        log_from=log_from,
        log_to=log_to,
    )


@app.route("/admin/invite", methods=["POST"])
@role_required("ADMIN")
def invite_officer():
    invitee_name = (request.form.get("invitee_name") or "").strip()
    invitee_contact = (request.form.get("invitee_contact") or "").strip()
    employee_ref = (request.form.get("employee_ref") or "").strip()

    if not all([invitee_name, invitee_contact, employee_ref]):
        flash(t("msg_fields_required"), "danger")
        return redirect(url_for("admin_dashboard"))

    raw_code = f"MUBAS-OFF-{secrets.token_hex(8).upper()}"
    token_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
    expires_at = (
        datetime.utcnow() + timedelta(hours=72)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO officer_invitation
                (invitee_name, invitee_contact, employee_ref, token_hash,
                 issued_by, expires_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (invitee_name, invitee_contact, employee_ref, token_hash,
             session["user_id"], expires_at),
        )
        conn.commit()
        log_event("INVITATION_ISSUED", "SUCCESS", user_id=session["user_id"],
                  detail=f"Issued invitation for {invitee_name}")
        flash(t("msg_invite_success", code=raw_code), "success")
    except Exception as exc:
        conn.rollback()
        log_event("INVITATION_ISSUED", "FAILURE", user_id=session["user_id"],
                  detail=str(exc))
        flash(t("msg_invite_error"), "danger")
    finally:
        conn.close()

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/invitations/<int:invitation_id>/revoke", methods=["POST"])
@role_required("ADMIN")
def revoke_invitation(invitation_id):
    conn = get_db()
    cursor = conn.cursor()
    invite = cursor.execute(
        "SELECT * FROM officer_invitation WHERE invitation_id = ?;",
        (invitation_id,),
    ).fetchone()
    if not invite or invite["status"] != "PENDING":
        conn.close()
        flash(t("msg_no_permission"), "danger")
        return redirect(url_for("admin_dashboard"))

    cursor.execute(
        "UPDATE officer_invitation SET status = 'REVOKED' WHERE invitation_id = ?;",
        (invitation_id,),
    )
    conn.commit()
    conn.close()
    log_event("INVITATION_REVOKED", "SUCCESS", user_id=session["user_id"],
              detail=f"Revoked invitation {invitation_id}")
    flash(t("msg_invite_revoked"), "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/status", methods=["POST"])
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
        conn.close()
        abort(404)

    if action == "deactivate":
        if user_id == session["user_id"]:
            conn.close()
            flash(t("msg_self_deactivate"), "danger")
            return redirect(url_for("admin_dashboard"))
        if target["role_name"] == "ADMIN" and _active_admin_count(conn, user_id) == 0:
            conn.close()
            log_event("USER_STATUS_CHANGE", "DENIED", user_id=session["user_id"],
                      target_user_id=user_id,
                      detail="Attempt to deactivate last active Administrator")
            flash(t("msg_last_admin"), "danger")
            return redirect(url_for("admin_dashboard"))
        new_status = "DEACTIVATED"
        message = t("msg_user_deactivated")
    else:
        new_status = "ACTIVE"
        message = t("msg_user_activated")

    conn.execute(
        "UPDATE app_user SET account_status = ?, failed_attempts = 0, "
        "locked_until = NULL WHERE user_id = ?;",
        (new_status, user_id),
    )
    conn.commit()
    conn.close()
    log_event("USER_STATUS_CHANGE", "SUCCESS", user_id=session["user_id"],
              target_user_id=user_id, detail=f"Status set to {new_status}")
    flash(message, "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@role_required("ADMIN")
def reset_user_password(user_id):
    conn = get_db()
    target = conn.execute(
        "SELECT * FROM app_user WHERE user_id = ?;", (user_id,)
    ).fetchone()
    if not target:
        conn.close()
        abort(404)

    temporary_password = "Mubas-" + secrets.token_urlsafe(6).replace("-", "").replace("_", "")
    conn.execute(
        """
        UPDATE app_user
        SET password_hash = ?, must_change_password = 1,
            failed_attempts = 0, locked_until = NULL
        WHERE user_id = ?;
        """,
        (generate_password_hash(temporary_password, method="pbkdf2:sha256"), user_id),
    )
    conn.commit()
    conn.close()
    log_event("PASSWORD_RESET", "SUCCESS", user_id=session["user_id"],
              target_user_id=user_id, detail="Administrator reset password")
    flash(t("msg_password_reset", password=temporary_password), "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/activate", methods=["GET", "POST"])
def activate_officer():
    """Single-use, contact-verified officer activation (FR-03)."""
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        contact = (request.form.get("contact") or "").strip()
        username = (request.form.get("username") or "").strip()
        full_name = (request.form.get("full_name") or "").strip()
        password = request.form.get("password") or ""

        if not all([code, contact, username, full_name, password]):
            flash(t("msg_fields_required"), "danger")
            return redirect(url_for("activate_officer"))
        if len(password) < 8:
            flash(t("msg_password_short"), "danger")
            return redirect(url_for("activate_officer"))

        token_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        conn = get_db()
        cursor = conn.cursor()
        invite = cursor.execute(
            """
            SELECT * FROM officer_invitation
            WHERE token_hash = ? AND status = 'PENDING' AND expires_at > ?;
            """,
            (token_hash, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")),
        ).fetchone()

        if not invite or invite["invitee_contact"].strip().lower() != contact.lower():
            if invite:
                log_event("REGISTER_OFFICER", "FAILURE",
                          detail="Activation contact did not match invitation")
            conn.close()
            flash(t("msg_activation_invalid"), "danger")
            return redirect(url_for("activate_officer"))

        email = contact if "@" in contact else None
        phone = None if "@" in contact else contact
        try:
            password_hash = generate_password_hash(password, method="pbkdf2:sha256")
            cursor.execute(
                """
                INSERT INTO app_user
                    (full_name, username, email, phone_number, password_hash,
                     role_id, created_by)
                VALUES (?, ?, ?, ?, ?, 2, ?);
                """,
                (full_name, username, email, phone, password_hash,
                 invite["issued_by"]),
            )
            new_user_id = cursor.lastrowid
            cursor.execute(
                """
                UPDATE officer_invitation
                SET status = 'USED', used_by = ?, used_at = ?
                WHERE invitation_id = ?;
                """,
                (new_user_id, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                 invite["invitation_id"]),
            )
            conn.commit()
            log_event("REGISTER_OFFICER", "SUCCESS", user_id=new_user_id,
                      detail="Extension officer activated via invitation")
            flash(t("msg_activation_success"), "success")
            return redirect(url_for("login"))
        except Exception as exc:
            conn.rollback()
            log_event("REGISTER_OFFICER", "FAILURE", detail=str(exc))
            flash(t("msg_activation_failed"), "danger")
        finally:
            conn.close()

    return render_template("activate.html")


@app.route("/set_language/<lang_code>")
def set_language(lang_code):
    if lang_code in LANGUAGES:
        session["lang"] = lang_code
    target = _safe_redirect_target(request.args.get("next"))
    return redirect(target or url_for("index"))


# ---------------------------------------------------------------------------
# Health and error handling
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    database_ok = True
    try:
        conn = get_db()
        conn.execute("SELECT COUNT(*) FROM app_user;").fetchone()
        conn.close()
    except sqlite3.Error:
        database_ok = False
    status = "ok" if database_ok else "degraded"
    return (
        jsonify(
            status=status,
            database="ok" if database_ok else "error",
            model_loaded=MODEL_LOADED,
            model_version=MODEL_VERSION,
            timestamp=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        200 if database_ok else 503,
    )


def _wants_json():
    return (
        request.path.startswith("/predict")
        or request.path.startswith("/health")
        or request.accept_mimetypes.best == "application/json"
    )


def _handle_http_error(error):
    code = getattr(error, "code", 500)
    custom = getattr(error, "description", None) if code == 400 else None
    message = custom or t(f"error.{code}")
    if code >= 500:
        app.logger.error("HTTP %s: %s", code, getattr(error, "description", error))
        log_event("SYSTEM_ERROR", "FAILURE", user_id=session.get("user_id"),
                  detail=f"HTTP {code}: {getattr(error, 'description', error)}")
    if _wants_json():
        return jsonify(success=False, error=message, code=code), code
    return render_template("error.html", code=code, message=message), code


for _code in (400, 401, 403, 404, 413, 429, 500):
    app.register_error_handler(_code, _handle_http_error)


ensure_reference_data()


def _start_http_redirect(http_port, https_port, lan_ip, allowed_hosts):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    allowed = {host.lower() for host in allowed_hosts if host}

    class Handler(BaseHTTPRequestHandler):
        def _target(self):
            host = (self.headers.get("Host", "").split(":")[0] or "").strip().lower()
            if host not in allowed:
                host = lan_ip
            return f"https://{host}:{https_port}{self.path}"

        def _redirect(self, code):
            self.send_response(code)
            self.send_header("Location", self._target())
            self.send_header("Connection", "close")
            self.end_headers()

        def do_GET(self):
            self._redirect(302)

        def do_HEAD(self):
            self._redirect(302)

        def do_POST(self):
            self._redirect(307)

        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", http_port), Handler)
    server.daemon_threads = True
    Thread(target=server.serve_forever, daemon=True).start()
    return server


def _build_ssl_context(cert_file, key_file):
    import ssl

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    context.load_cert_chain(cert_file, key_file)
    return context


if __name__ == "__main__":
    http_port = int(os.environ.get("PORT", "5000"))
    https_port = int(os.environ.get("HTTPS_PORT", "5443"))
    ssl_cert = os.environ.get("SSL_CERT")
    ssl_key = os.environ.get("SSL_KEY")
    ips = None
    ca_file = None

    if ssl_cert and ssl_key:
        ssl_context = _build_ssl_context(ssl_cert, ssl_key)
    elif os.environ.get("USE_HTTPS", "1") == "0":
        ssl_context = None
    else:
        try:
            from run_https import ensure_certificate

            cert_file, key_file, ips, ca_file = ensure_certificate()
            ssl_context = _build_ssl_context(str(cert_file), str(key_file))
        except Exception as exc:
            ssl_context = None
            print(f"[!] HTTPS unavailable: {exc}")
            print("[!] Starting HTTP. Live camera will not work on LAN clients.")

    if ssl_context:
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["PREFERRED_URL_SCHEME"] = "https"
        try:
            from run_https import lan_ips

            ips = ips or lan_ips()
        except Exception:
            ips = ["127.0.0.1"]
        lan_ip = next((ip for ip in ips if ip != "127.0.0.1"), "127.0.0.1")
        if http_port != https_port:
            try:
                _start_http_redirect(
                    http_port, https_port, lan_ip,
                    set(ips) | {"localhost", socket.gethostname()},
                )
                print(f"[+] HTTP redirect: http://{lan_ip}:{http_port} -> https://{lan_ip}:{https_port}")
            except OSError as exc:
                print(f"[!] HTTP redirect unavailable on port {http_port}: {exc}")
        print(f"[+] HTTPS enabled: https://{lan_ip}:{https_port}")
        if ca_file:
            print(f"    Install this CA on each client once: {ca_file}")
        print("    TLS 1.2+ is enforced; live camera requires this HTTPS origin.")
        app.run(
            host="0.0.0.0",
            port=https_port,
            debug=os.environ.get("FLASK_DEBUG", "0") == "1",
            ssl_context=ssl_context,
            use_reloader=False,
        )
    else:
        app.run(
            host="0.0.0.0",
            port=http_port,
            debug=os.environ.get("FLASK_DEBUG", "0") == "1",
        )
