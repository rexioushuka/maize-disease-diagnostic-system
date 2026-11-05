"""Application factory (fixes QUA-01: no monolithic module-level app)."""

import os

from flask import Flask

from . import config as config_module
from .db import register_db
from .security_core import register_security
from .services.model_loader import build_model


def create_app(config_name=None, overrides=None):
    config_name = config_name or os.environ.get("APP_CONFIG", "default")
    config_obj = config_module.CONFIG_MAP.get(config_name, config_module.DevelopmentConfig)

    root = config_module.BASE_DIR
    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder=os.path.join(root, "templates"),
        static_folder=os.path.join(root, "static"),
    )
    app.config.from_object(config_obj)
    if overrides:
        app.config.update(overrides)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    init_app = getattr(config_obj, "init_app", None)
    if callable(init_app):
        init_app(app)

    # Optional audit HMAC key derived from SECRET_KEY when unset.
    app.config.setdefault("AUDIT_HMAC_KEY", os.environ.get("AUDIT_HMAC_KEY"))

    register_db(app)
    register_security(app)

    from .services.model_registry import load_mapping

    mapping = load_mapping(app.config["CLASS_MAPPING_PATH"])
    app.config["CLASS_NAMES"] = mapping["class_names"]
    app.config["MODEL_VERSION"] = mapping["model_version"]
    app.config["MODEL_INPUT_SIZE"] = mapping["input_size"]

    try:
        with app.app_context():
            app.extensions["maize_model"] = build_model()
        app.extensions["model_loaded"] = True
    except Exception as exc:  # fail loudly but allow /health to report degraded
        app.logger.error("MODEL LOAD FAILED: %s", exc)
        app.extensions["maize_model"] = None
        app.extensions["model_loaded"] = False
        if config_name == "production":
            raise

    _register_blueprints(app)
    _register_jinja(app)
    _register_errors(app)

    from .bootstrap import ensure_reference_data

    with app.app_context():
        try:
            ensure_reference_data()
        except Exception as exc:  # DB may not be initialised yet (first boot)
            app.logger.warning("Reference data not verified: %s", exc)

    return app


def _register_blueprints(app):
    from .blueprints.auth import bp as auth_bp
    from .blueprints.farmer import bp as farmer_bp
    from .blueprints.officer import bp as officer_bp
    from .blueprints.admin import bp as admin_bp
    from .blueprints.main import bp as main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(farmer_bp)
    app.register_blueprint(officer_bp)
    app.register_blueprint(admin_bp)


def _register_jinja(app):
    from .i18n import LANGUAGES
    from .security_core import csrf_token, csp_nonce, t
    from .services.labels import class_label, role_label, status_label
    from datetime import datetime, timezone

    @app.context_processor
    def inject_globals():
        from flask import session
        return {
            "t": t,
            "lang": session.get("lang", "en"),
            "languages": LANGUAGES,
            "csrf_token": csrf_token,
            "csp_nonce": csp_nonce,
            "current_year": datetime.now(timezone.utc).year,
            "session_idle_minutes": app.config["SESSION_IDLE_MINUTES"],
        }

    app.jinja_env.filters["role_label"] = role_label
    app.jinja_env.filters["status_label"] = status_label
    app.jinja_env.filters["class_label"] = class_label


def _register_errors(app):
    from flask import jsonify, render_template, request, session
    from .security_core import t
    from .services.audit import log_event

    def _wants_json():
        return (
            request.path.startswith("/predict")
            or request.path.startswith("/api/")
            or request.path.startswith("/health")
            or request.accept_mimetypes.best == "application/json"
        )

    def _handle(error):
        code = getattr(error, "code", 500)
        custom = getattr(error, "description", None) if code in (400, 401, 403) else None
        message = custom or t(f"error.{code}")
        if code >= 500:
            app.logger.error("HTTP %s: %s", code, getattr(error, "description", error))
            log_event("SYSTEM_ERROR", "FAILURE", user_id=session.get("user_id"),
                      detail=f"HTTP {code}: {getattr(error, 'description', error)}")
        if _wants_json():
            return jsonify(success=False, error=message, code=code), code
        return render_template("error.html", code=code, message=message), code

    for code in (400, 401, 403, 404, 413, 429, 500):
        app.register_error_handler(code, _handle)
