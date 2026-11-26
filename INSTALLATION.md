# Installation Guide

This guide walks through cloning the Maize Crop Disease Diagnostic System from GitHub, preparing a local Python environment, initializing the database, and running the application on your PC.

## 1. Prerequisites

Install the following before starting:

| Requirement | Recommended | Notes |
|---|---|---|
| Git | Latest stable | Used to clone the repository. |
| Python | 3.10+ (3.10 recommended) | The Docker image uses Python 3.10. Use a Python version compatible with the pinned packages in `requirements.txt`. |
| pip | Bundled with Python | Used to install dependencies. |
| Virtual environment tool | `venv` (built-in) | Keeps project dependencies isolated. |
| Disk space | ~2 GB free | Includes virtual environment, TensorFlow, and model artifact. |

Optional:

- **OpenSSL** - needed only for `run_https.py` local HTTPS certificates (Git for Windows usually includes it).
- **Docker Desktop** - needed only for Docker deployment.

## 2. Clone the Repository

Open a terminal (PowerShell on Windows, Terminal on macOS/Linux) and run:

```bash
git clone https://github.com/rexioushuka/maize-disease-diagnostic-system.git
cd maize-disease-diagnostic-system
```

## 3. Create and Activate a Virtual Environment

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks script activation, run this once in the same terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Windows (Command Prompt)

```bat
python -m venv .venv
.venv\Scripts\activate.bat
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

After activation, your prompt should show `(.venv)`.

## 4. Install Dependencies

For normal local development and testing:

```bash
pip install --upgrade pip
pip install -r requirements-dev.txt
```

`requirements-dev.txt` includes `requirements.txt`, so runtime dependencies are installed automatically.

For a runtime-only install (no dev/test/training tools):

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## 5. Configure Environment Variables

The app can start with local defaults, but you should set key values explicitly.

### Minimum recommended local configuration

#### Windows (PowerShell)

```powershell
$env:APP_CONFIG = "development"
$env:SECRET_KEY = "replace-with-a-long-random-secret"
$env:LOAD_MODEL = "1"
```

#### macOS / Linux (bash)

```bash
export APP_CONFIG="development"
export SECRET_KEY="replace-with-a-long-random-secret"
export LOAD_MODEL="1"
```

### Important variables

| Variable | Purpose | Default / Behavior |
|---|---|---|
| `APP_CONFIG` | Config mode: `development`, `production`, `testing` | `development` |
| `SECRET_KEY` | Flask session/signing secret | Required in production; dev can use local/generated key |
| `DB_PATH` | SQLite database path | `maize_system.db` in project root |
| `UPLOAD_FOLDER` | Evidence image upload directory | `instance/uploads` |
| `LOAD_MODEL` | Load ML model on startup (`1`/`0`) | `1` |
| `MODEL_HASH_VERIFY` | Verify model SHA-256 from `models/class_mapping.json` | `1` |
| `SESSION_COOKIE_SECURE` | Secure cookies over HTTPS | `0` by default in dev |
| `PORT` | HTTP port for `app.py`/`wsgi.py` | `5000` |

### Initial administrator account

On first database initialization, `init_db.py` creates an admin only if no admin exists. Set these before initializing:

#### Windows (PowerShell)

```powershell
$env:MAIZE_ADMIN_USERNAME = "admin"
$env:MAIZE_ADMIN_EMAIL = "admin@example.com"
$env:MAIZE_ADMIN_PASSWORD = "UseAStrongPassword123!"
```

#### macOS / Linux (bash)

```bash
export MAIZE_ADMIN_USERNAME="admin"
export MAIZE_ADMIN_EMAIL="admin@example.com"
export MAIZE_ADMIN_PASSWORD="UseAStrongPassword123!"
```

If `MAIZE_ADMIN_PASSWORD` is not set, `init_db.py` generates a temporary password and prints it in the terminal. The seeded admin is marked to change password on first login.

### Optional Brevo configuration

Brevo is used for registration verification and out-of-band secret delivery when configured. You can set Brevo variables in the environment or create `instance/brevo.env` (this path is gitignored).

Example `instance/brevo.env`:

```env
BREVO_API_KEY=your_brevo_api_key
BREVO_SENDER_EMAIL=sender@example.com
BREVO_SENDER_NAME=Maize Disease System
BREVO_SMS_SENDER=MaizeSys
```

If Brevo is not configured, local/dev registration falls back to non-verified registration behavior and the app logs a warning.

## 6. Initialize the Database

Run the database initialization script:

```bash
python init_db.py
```

This creates/applies the SQLite schema, reference data, village sections, advisories, and seeds the initial admin if needed.

For an existing database where you only want migrations:

```bash
python init_db.py --migrate-only
```

## 7. Run the Application

### Option A: Standard local development (HTTP)

```bash
python app.py
```

By default, the app runs at:

```text
http://127.0.0.1:5000
```

Open that URL in your browser.

> Note: Browser camera access may be blocked on non-localhost plain HTTP pages. For LAN camera testing, use HTTPS option below.

### Option B: Local HTTPS for LAN/camera testing

```bash
python run_https.py
```

This launcher:

- creates/uses a local CA and server certificate in `instance/certs/`
- starts HTTPS on port `5443` by default
- starts an HTTP-to-HTTPS redirect on port `5000`

Open the HTTPS URL printed in the terminal, for example:

```text
https://<your-lan-ip>:5443
```

For camera access on other devices, install/trust `instance/certs/maize-ca-cert.crt` on the client device, or accept the browser warning for local testing.

### Option C: Production-style local WSGI

```bash
gunicorn --bind 0.0.0.0:5000 --workers 1 --threads 4 --timeout 120 wsgi:app
```

Use this only where Gunicorn is available/suitable for your environment. On Windows, prefer `python app.py`, `run_https.py`, or Docker.

## 8. First Login and Basic Use

1. Open the app in your browser.
2. Sign in with the admin credentials you configured before `init_db.py`.
3. If the admin was seeded with a temporary/generated password, you will be required to change it on first login.
4. As admin, you can manage users, issue officer/admin invitations, assign officer sections, and review audit logs.
5. Farmers can register from the registration page; diagnosis is available from the farmer dashboard after login.

## 9. Verify the Installation

### Check the health endpoint

With the app running, open:

```text
http://127.0.0.1:5000/health
```

A healthy response should report database and model status.

### Run tests (optional but recommended)

```bash
pytest tests -q
```

For lightweight test runs without loading TensorFlow model:

#### Windows (PowerShell)

```powershell
$env:LOAD_MODEL = "0"
$env:APP_CONFIG = "testing"
pytest tests -q
```

#### macOS / Linux (bash)

```bash
LOAD_MODEL=0 APP_CONFIG=testing pytest tests -q
```

## 10. Optional: Docker Setup

Build the image:

```bash
docker build -t maize-disease-system .
```

Run the container:

```bash
docker run --rm -p 5000:5000 \
  -e SECRET_KEY="replace-with-a-long-random-secret" \
  -e MAIZE_ADMIN_EMAIL="admin@example.com" \
  -e MAIZE_ADMIN_PASSWORD="UseAStrongPassword123!" \
  -v maize_data:/app/data \
  -v maize_instance:/app/instance \
  -v maize_backups:/app/backups \
  maize-disease-system
```

Windows PowerShell version:

```powershell
docker run --rm -p 5000:5000 `
  -e SECRET_KEY="replace-with-a-long-random-secret" `
  -e MAIZE_ADMIN_EMAIL="admin@example.com" `
  -e MAIZE_ADMIN_PASSWORD="UseAStrongPassword123!" `
  -v maize_data:/app/data `
  -v maize_instance:/app/instance `
  -v maize_backups:/app/backups `
  maize-disease-system
```

Then open `http://127.0.0.1:5000`.

## 11. Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `pip install` fails for TensorFlow | Python version/package compatibility | Use Python 3.10 (recommended) or a compatible version; upgrade `pip`; install in a clean virtual environment. |
| `ModuleNotFoundError` | Virtual environment not activated or deps not installed | Activate `.venv` and run `pip install -r requirements-dev.txt`. |
| App starts but model load fails | Missing/corrupt model or hash mismatch | Ensure `models/mobilenetv2_maize.keras` and `models/class_mapping.json` exist. Set `MODEL_HASH_VERIFY=0` only for local debugging, not production. |
| Cannot log in as admin | Admin not seeded with expected credentials | Check terminal output from `init_db.py`. If DB already had an admin, `init_db.py` will not create another. Use `python manage.py create-admin ...` if needed. |
| Camera does not work on LAN HTTP | Browser secure-context restriction | Run `python run_https.py` and use HTTPS. |
| `run_https.py` cannot find OpenSSL | OpenSSL not installed/in PATH | Install Git for Windows/OpenSSL and retry. |
| Port already in use | Another process using 5000/5443 | Stop the conflicting process or set `PORT` / `HTTPS_PORT`. |
| Brevo verification fails | Missing/invalid Brevo config or IP restrictions | Check `BREVO_*` settings, sender configuration, and Brevo account IP/API restrictions. |

## 12. Useful Commands

```bash
# Initialize database
python init_db.py

# Apply migrations only
python init_db.py --migrate-only

# Run dev server
python app.py

# Run HTTPS local server
python run_https.py

# Create admin via CLI
python manage.py create-admin --username admin2 --email admin2@example.com --password "StrongPassword123!"

# Verify audit chain
python manage.py verify-audit

# Run tests
pytest tests -q
```
