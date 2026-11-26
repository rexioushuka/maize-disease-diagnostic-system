# Maize Crop Disease Diagnostic System

A Flask-based web system for diagnosing maize leaf diseases from images. The application supports farmer self-registration with contact verification, role-based access for farmers/extension officers/administrators, rule-based bilingual advisories (English/Chichewa), audit logging, and evidence/reporting workflows.

## Overview

The system classifies a maize leaf image into one of four classes:

| Class | Type |
|---|---|
| `Downy_Mildew` | Disease |
| `Healthy` | Healthy |
| `MLN` | Disease |
| `MSV` | Disease |

The active model artifact is `models/mobilenetv2_maize.keras` (`MobileNetV2-v1.1`), with authoritative class order and hash in `models/class_mapping.json`.

## Key Features

- **Diagnosis**: Upload/capture a maize leaf image and receive predicted class, confidence, class probabilities, and IPM advisory.
- **OOD/low-confidence handling**: Low-confidence or out-of-distribution results are flagged and not recorded as confirmed disease cases.
- **Role-based access**: `FARMER`, `EXTENSION_OFFICER`, and `ADMIN` roles with server-side enforcement.
- **Farmer workflow**: Registration, contact verification code, diagnosis history, password change/reset.
- **Officer workflow**: Scoped dashboard by assigned village sections, filters, and anonymised CSV export.
- **Admin workflow**: User management, officer/admin invitations, section assignment, audit log view, audit-chain verification.
- **Security controls**: CSRF protection, password policy, account lockout/rate limiting, session idle/absolute timeout, secure headers, safe uploads, hashed secrets/tokens.
- **Auditability**: Append-only `system_log` with HMAC hash chain verification.
- **Operations**: SQLite WAL DB, backup utility, image retention purge, Docker image, HTTPS LAN launcher.

## Tech Stack

- **Backend**: Python, Flask, Werkzeug, Gunicorn
- **ML**: TensorFlow/Keras MobileNetV2, Pillow, NumPy
- **Database**: SQLite (`maize_schema.sql`)
- **Frontend**: Jinja templates, Bootstrap, vanilla JS
- **Testing**: pytest
- **Deployment**: Docker, nginx TLS reverse-proxy config, local HTTPS launcher

## Project Structure

```text
.
├── app.py                      # Dev entrypoint (creates msds app)
├── wsgi.py                     # WSGI/Gunicorn entrypoint
├── run_https.py                # Local HTTPS launcher with local CA
├── init_db.py                  # DB initialization/migrations/seed data
├── manage.py                   # Operational CLI
├── backup_db.py                # Online SQLite backup
├── maize_schema.sql            # SQLite schema, triggers, indexes, seed refs
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # Dev/test/training dependencies
├── Dockerfile                  # Production container image
├── docker-entrypoint.sh        # Container startup/migrations/background jobs
├── deploy/nginx.conf           # TLS reverse proxy example
├── msds/                       # Application package
│   ├── __init__.py             # App factory
│   ├── config.py               # Environment/config classes
│   ├── db.py                   # Request-scoped SQLite connection
│   ├── security_core.py        # CSRF, role guards, session, headers
│   ├── blueprints/             # auth, farmer, officer, admin, main routes
│   └── services/               # diagnosis, audit, validation, brevo, model
├── models/                     # Model, class mapping, evaluation artifacts
├── templates/                  # Jinja HTML templates
├── static/                     # CSS/JS/vendor assets
├── tests/                      # Pytest suite
└── report_evidence/ch5-8/      # Report evidence artifacts/screenshots
```

## Quick Start (Local Development)

### 1. Prerequisites

- Python 3.10+ recommended (project includes Docker base `python:3.10-slim`; local evidence also ran on Python 3.13).
- `pip`
- A virtual environment

### 2. Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

For runtime-only installs:

```powershell
pip install -r requirements.txt
```

### 3. Configure Environment

The app can run with defaults for local development. For production or full functionality, set environment variables.

Common variables:

```powershell
$env:APP_CONFIG="development"          # development | production | testing
$env:SECRET_KEY="replace-with-strong-secret"
$env:DB_PATH="C:\path\to\maize_system.db"
$env:UPLOAD_FOLDER="C:\path\to\uploads"
$env:LOAD_MODEL="1"                    # 0 for lightweight testing
$env:MODEL_HASH_VERIFY="1"
$env:SESSION_COOKIE_SECURE="0"         # 1 behind HTTPS
```

Initial admin seeding (used by `init_db.py` when no admin exists):

```powershell
$env:MAIZE_ADMIN_USERNAME="admin"
$env:MAIZE_ADMIN_EMAIL="admin@example.com"
$env:MAIZE_ADMIN_PASSWORD="UseAStrongPassword123!"
```

Brevo contact verification/delivery (optional; stored in env or `instance/brevo.env`):

```text
BREVO_API_KEY=...
BREVO_SENDER_EMAIL=...
BREVO_SENDER_NAME=Maize Disease System
BREVO_SMS_SENDER=MaizeSys
# Optional SMTP fallback for email:
BREVO_SMTP_LOGIN=...
BREVO_SMTP_KEY=...
BREVO_SMTP_HOST=smtp-relay.brevo.com
BREVO_SMTP_PORT=587
```

If Brevo is not configured, registration falls back to unverified local/dev behavior and secret delivery falls back to configured/logged channels.

### 4. Initialize Database

```powershell
python init_db.py
```

For existing DB migrations only:

```powershell
python init_db.py --migrate-only
```

### 5. Run

Development HTTP server:

```powershell
python app.py
```

The app warns that plain HTTP on non-localhost can block browser camera access. For LAN camera support, use HTTPS:

```powershell
python run_https.py
```

Production-style WSGI:

```powershell
gunicorn --bind 0.0.0.0:5000 --workers 1 --threads 4 --timeout 120 wsgi:app
```

## Docker

Build:

```powershell
docker build -t maize-disease-system .
```

Run:

```powershell
docker run --rm -p 5000:5000 `
  -e SECRET_KEY="replace-with-strong-secret" `
  -e MAIZE_ADMIN_EMAIL="admin@example.com" `
  -e MAIZE_ADMIN_PASSWORD="UseAStrongPassword123!" `
  -v maize_data:/app/data `
  -v maize_instance:/app/instance `
  -v maize_backups:/app/backups `
  maize-disease-system
```

The container initializes/migrates the DB on startup, runs as a non-root user, exposes `/health`, and uses a single Gunicorn worker to keep one model instance in memory.

## Operational CLI

```powershell
python manage.py purge-images --days 90
python manage.py purge-images --days 90 --dry-run
python manage.py verify-audit
python manage.py create-admin --username admin2 --email admin2@example.com --password "StrongPassword123!"
python manage.py rehash-model
```

## Testing

Run the test suite:

```powershell
$env:LOAD_MODEL="0"
$env:APP_CONFIG="testing"
pytest tests -q
```

The repository also includes report evidence artifacts under `report_evidence/ch5-8/`, including test logs, DB safeguard checks, latency runs, and screenshots.

## Model and Evaluation

- Model: `models/mobilenetv2_maize.keras`
- Mapping/hash: `models/class_mapping.json`
- Evaluation report: `models/evaluation_report_v2.json`
- Confusion matrix: `models/confusion_matrix_v2.png`

Current recorded evaluation (`models/evaluation_report_v2.json`, 1,873 test samples):

| Metric | Value |
|---|---:|
| Accuracy | 0.9712 |
| Macro F1 | 0.9695 |
| Weighted F1 | 0.9711 |

Evaluate caution when interpreting results: dataset split/leakage and local-field validation limitations are documented in project evidence.

## Security Notes

- Do not commit secrets, `.env` files, certificates, private keys, or runtime DB/uploads.
- Set a strong `SECRET_KEY` in production; production config rejects ephemeral default startup.
- Use HTTPS in deployment; camera access on non-localhost browsers requires a secure context.
- Invitation codes and temporary passwords are delivered out-of-band when delivery channels are configured; avoid exposing secrets in UI/logs.
- Review `CHANGELOG.md` and `report_evidence/ch5-8/CHAPTERS_5-8_EVIDENCE_PACK.md` for implementation status and verified evidence.

## License

No license file is currently included in this repository. Add a `LICENSE` file if you intend to distribute the project publicly under specific terms.
