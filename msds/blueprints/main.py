"""Public routes: landing, language switch, health."""

from datetime import datetime, timezone

from flask import Blueprint, jsonify, redirect, request, session, url_for

from ..i18n import LANGUAGES
from ..security_core import safe_redirect_target

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    if "user_id" in session:
        role = session.get("role_name")
        if role == "FARMER":
            return redirect(url_for("farmer.dashboard"))
        if role == "EXTENSION_OFFICER":
            return redirect(url_for("officer.dashboard"))
        if role == "ADMIN":
            return redirect(url_for("admin.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/set_language/<lang_code>")
def set_language(lang_code):
    if lang_code in LANGUAGES:
        session["lang"] = lang_code
    target = safe_redirect_target(request.args.get("next"))
    return redirect(target or url_for("main.index"))


@bp.route("/health")
def health():
    from flask import current_app

    from ..db import get_db

    database_ok = True
    try:
        get_db().execute("SELECT COUNT(*) FROM app_user;").fetchone()
    except Exception:
        database_ok = False
    model_loaded = bool(current_app.extensions.get("model_loaded"))
    model = current_app.extensions.get("maize_model")
    if model_loaded and model is not None and not hasattr(model, "predict"):
        model_loaded = False
    status = "ok" if (database_ok and model_loaded) else "degraded"
    code = 200 if status == "ok" else 503
    return (
        jsonify(
            status=status,
            database="ok" if database_ok else "error",
            model_loaded=model_loaded,
            model_version=current_app.config.get("MODEL_VERSION"),
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        code,
    )
