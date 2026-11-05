"""Configuration objects for the maize disease system.

All secrets come from the environment in production.  Development falls back
to a locally persisted key so the app still boots on a fresh checkout, but a
missing SECRET_KEY in production is a hard error (see :meth:`ProductionConfig.init_app`).
"""

import os
import secrets
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")


def _load_instance_env():
    """Load KEY=VALUE pairs from instance/brevo.env into os.environ.

    instance/ is gitignored, so secrets stay out of the repository.  Real
    environment variables take precedence (setdefault).
    """
    path = os.path.join(INSTANCE_DIR, "brevo.env")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_instance_env()


def _load_or_create_key(instance_dir):
    key_path = os.path.join(instance_dir, "secret_key")
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


class Config:
    BASE_DIR = BASE_DIR
    INSTANCE_DIR = os.path.join(BASE_DIR, "instance")

    DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "maize_system.db"))
    SCHEMA_PATH = os.path.join(BASE_DIR, "maize_schema.sql")
    MODEL_DIR = os.environ.get("MODEL_DIR", os.path.join(BASE_DIR, "models"))
    MODEL_PATH = os.environ.get(
        "MODEL_PATH", os.path.join(BASE_DIR, "models", "mobilenetv2_maize.keras")
    )
    CLASS_MAPPING_PATH = os.environ.get(
        "CLASS_MAPPING_PATH", os.path.join(BASE_DIR, "models", "class_mapping.json")
    )
    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER", os.path.join(BASE_DIR, "instance", "uploads")
    )

    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}
    MAX_UPLOAD_BYTES = 5 * 1024 * 1024          # FR-07
    IMAGE_RETENTION_DAYS = int(os.environ.get("IMAGE_RETENTION_DAYS", "90"))
    CONFIDENCE_THRESHOLD = 0.70                  # FR-11
    OOD_ENTROPY_THRESHOLD = float(os.environ.get("OOD_ENTROPY_THRESHOLD", "1.10"))
    OOD_MARGIN_THRESHOLD = float(os.environ.get("OOD_MARGIN_THRESHOLD", "0.15"))

    SESSION_IDLE_MINUTES = int(os.environ.get("SESSION_IDLE_MINUTES", "30"))
    SESSION_ABSOLUTE_HOURS = int(os.environ.get("SESSION_ABSOLUTE_HOURS", "12"))

    # Lockout / throttling
    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_LOCK_MINUTES = 15
    LOGIN_RATE_LIMIT = 10
    LOGIN_RATE_WINDOW = 60
    DIAGNOSIS_RATE_LIMIT = 20
    DIAGNOSIS_RATE_WINDOW = 60
    PASSWORD_RESET_TTL_MINUTES = int(os.environ.get("PASSWORD_RESET_TTL_MINUTES", "30"))

    # Model loading can be disabled for tests.
    LOAD_MODEL = os.environ.get("LOAD_MODEL", "1") == "1"
    MODEL_HASH_VERIFY = os.environ.get("MODEL_HASH_VERIFY", "1") == "1"

    SECRET_KEY = os.environ.get("SECRET_KEY") or _load_or_create_key(INSTANCE_DIR)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_BYTES
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=SESSION_IDLE_MINUTES)
    JSON_SORT_KEYS = False
    PREFERRED_URL_SCHEME = os.environ.get("PREFERRED_URL_SCHEME", "https")

    # Reverse proxy trust: comma separated list of hop counts, e.g. "1".
    TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))
    HSTS_MAX_AGE = int(os.environ.get("HSTS_MAX_AGE", "31536000"))

    # Out-of-band notification hooks (optional). When configured, invite codes
    # and password-reset tokens are delivered here instead of the UI.
    MAIL_DSN = os.environ.get("MAIL_DSN", "")
    SMS_GATEWAY_DSN = os.environ.get("SMS_GATEWAY_DSN", "")

    # Brevo transactional email/SMS. Email can be delivered via the REST API
    # (BREVO_API_KEY, also required for SMS) or via SMTP relay (BREVO_SMTP_*).
    BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
    BREVO_SMTP_HOST = os.environ.get("BREVO_SMTP_HOST", "smtp-relay.brevo.com")
    BREVO_SMTP_PORT = int(os.environ.get("BREVO_SMTP_PORT", "587"))
    BREVO_SMTP_LOGIN = os.environ.get("BREVO_SMTP_LOGIN", "")
    BREVO_SMTP_KEY = os.environ.get("BREVO_SMTP_KEY", "")
    BREVO_SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL", "")
    BREVO_SENDER_NAME = os.environ.get("BREVO_SENDER_NAME", "Maize Disease System")
    BREVO_SMS_SENDER = os.environ.get("BREVO_SMS_SENDER", "MaizeSys")


class DevelopmentConfig(Config):
    DEBUG = False


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"
    TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))
    TESTING = False

    @classmethod
    def init_app(cls, app):
        if not os.environ.get("SECRET_KEY") and not os.path.exists(
            os.path.join(app.instance_path, "secret_key")
        ):
            raise RuntimeError(
                "SECRET_KEY must be set in production. Refusing to start with an "
                "ephemeral key."
            )


class TestingConfig(Config):
    TESTING = True
    LOAD_MODEL = os.environ.get("LOAD_MODEL", "0") == "1"
    WTF_CSRF_ENABLED = True
    SESSION_COOKIE_SECURE = False
    SECRET_KEY = "test-secret-key"
    TRUSTED_PROXY_HOPS = 0


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}
