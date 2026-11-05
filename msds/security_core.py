"""Security primitives: CSRF, role guards, session enforcement, headers."""

import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import abort, flash, redirect, request, session, url_for

from .db import get_db
from .extensions import rate_limiter
from .i18n import translate

CSRF_SESSION_KEY = "_csrf_token"
CSRF_ROTATE_KEY = "_csrf_rotated_at"


def utcnow():
    return datetime.now(timezone.utc)


def utc_stamp(dt=None):
    dt = dt or utcnow()
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return None


def t(key, **kwargs):
    return translate(session.get("lang", "en"), key, **kwargs)


def csrf_token():
    """Return the session CSRF token, creating it on first use."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def rotate_csrf_token():
    """Issue a fresh token (SEC-07: rotate on login/privilege change)."""
    session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    session[CSRF_ROTATE_KEY] = utc_stamp()


def safe_redirect_target(target):
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


def rate_key(prefix, *parts):
    """Build a limiter key from caller-controlled identity parts."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    ip = (ip.split(",")[0] or "").strip()
    joined = "|".join(str(p) for p in parts) + "|" + ip
    return f"{prefix}:{joined}"


def allow_request(prefix, limit, window, *parts):
    return rate_limiter.allow(rate_key(prefix, *parts), limit, window)


def role_required(*allowed_roles):
    def decorator(func):
        @wraps(func)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                from .services.audit import log_event

                log_event(
                    "UNAUTHORIZED_ATTEMPT",
                    "DENIED",
                    detail=f"Unauthenticated access to {request.path}",
                )
                abort(401, description=t("error.401"))
            if session.get("role_name") not in allowed_roles:
                from .services.audit import log_event

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


def register_security(app):
    @app.before_request
    def enforce_session_and_csrf():
        from .services.audit import log_event

        if request.endpoint == "static":
            return None

        if "user_id" in session:
            now = utcnow()

            # Absolute session lifetime (SEC-12).
            created = parse_utc(session.get("_created_at"))
            if created and now - created > timedelta(
                hours=app.config["SESSION_ABSOLUTE_HOURS"]
            ):
                user_id = session.get("user_id")
                session.clear()
                log_event("LOGOUT", "SUCCESS", user_id=user_id,
                          detail="Session ended after absolute lifetime")
                flash(t("msg_session_expired"), "warning")
                return redirect(url_for("auth.login"))

            # Idle-session timeout (FR-05 / NFR-06).
            last = parse_utc(session.get("_last_activity"))
            if last and now - last > timedelta(
                minutes=app.config["SESSION_IDLE_MINUTES"]
            ):
                user_id = session.get("user_id")
                session.clear()
                log_event("LOGOUT", "SUCCESS", user_id=user_id,
                          detail="Session expired after inactivity")
                flash(t("msg_session_expired"), "warning")
                return redirect(url_for("auth.login"))
            session["_last_activity"] = utc_stamp(now)

            # Deactivated/removed account loses access immediately (FR-17).
            account = get_db().execute(
                "SELECT account_status, must_change_password FROM app_user "
                "WHERE user_id = ?;",
                (session["user_id"],),
            ).fetchone()
            if not account or account["account_status"] != "ACTIVE":
                user_id = session.get("user_id")
                session.clear()
                log_event("LOGOUT", "SUCCESS", user_id=user_id,
                          detail="Session ended because the account is not active")
                flash(t("msg_account_disabled"), "danger")
                return redirect(url_for("auth.login"))

            session["must_change_password"] = bool(account["must_change_password"])
            if session.get("must_change_password") and request.endpoint not in {
                "auth.change_password", "auth.logout", "auth.set_language", "static",
            }:
                flash(t("msg_must_change"), "warning")
                return redirect(url_for("auth.change_password"))

        # CSRF protection for state-changing requests (NFR-06).
        if request.method == "POST":
            token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
            expected = session.get(CSRF_SESSION_KEY)
            if not token or not expected or not secrets.compare_digest(
                str(token), str(expected)
            ):
                log_event("SYSTEM_ERROR", "FAILURE", user_id=session.get("user_id"),
                          detail="CSRF token missing or invalid")
                abort(400, description=t("msg_csrf"))
        return None

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        nonce = getattr(request, "_csp_nonce", None)
        script_src = "'self'"
        style_src = "'self'"
        if nonce:
            script_src += f" 'nonce-{nonce}'"
            style_src += f" 'nonce-{nonce}'"
        response.headers.setdefault(
            "Content-Security-Policy",
            f"default-src 'self'; "
            f"style-src {style_src}; script-src {script_src}; "
            f"img-src 'self' data: blob:; media-src 'self' blob:; "
            f"font-src 'self'; connect-src 'self'; object-src 'none'; "
            f"base-uri 'self'; frame-ancestors 'none'",
        )
        response.headers.setdefault(
            "Permissions-Policy", "camera=(self), microphone=(), geolocation=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        # HSTS is emitted whenever the request is HTTPS *or* behind a trusted
        # proxy that reports HTTPS (SEC-18 / SEC-10).
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
        if request.is_secure or forwarded_proto == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={app.config['HSTS_MAX_AGE']}; includeSubDomains",
            )
        if session.get("user_id") and request.endpoint != "static":
            response.headers.setdefault(
                "Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers.setdefault("Pragma", "no-cache")
            response.headers.setdefault("Expires", "0")
        return response


def csp_nonce():
    if not hasattr(request, "_csp_nonce"):
        request._csp_nonce = secrets.token_urlsafe(16)
    return request._csp_nonce
