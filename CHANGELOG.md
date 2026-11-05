# Change Log

Project: Maize Crop Disease Diagnostic System  
Repository: https://github.com/rexioushuka/maize-disease-diagnostic-system  
Documented: 2026-11-04 at commit `798fca2`

This file records every change made to the project after the initial
repository baseline, including application code, security, deployment,
documentation, data, and verification work.

## 2026-11-05 - Password reset via email/SMS verification code

### Added

- Forgotten-password flow now verifies the requester with a Brevo-delivered
  6-digit code before any reset happens: `/forgot-password` issues a code to
  the registered email (fallback: registered SMS phone, and vice versa if the
  primary channel fails) and redirects to `/forgot-password/verify`; the code
  is SHA-256 hashed, 10-minute expiry, max 5 attempts, rate-limited, resend
  supported, anti-enumeration preserved (unknown identifiers get the identical
  response and page). On success a one-time `password_reset_token` is issued
  and the user is forwarded to the existing set-new-password page. New table
  `password_reset_code` with migration. Tests cover known/unknown identifier,
  correct/wrong/expired codes, and the full reset end-to-end (login with the
  new password).
- When Brevo is not configured the legacy administrator-assisted token flow is
  kept as-is.

## 2026-11-05 - Brevo-verified registration & LAN HTTPS camera access

### Added

- Farmer self-registration now verifies the given email/phone with a
  Brevo-delivered 6-digit code before the account is created. A pending
  `registration_verification` row (name, username, section, password hash,
  code hash, 10-minute expiry, max 5 attempts) is stored server-side and the
  user is created only after the code is entered on `/register/verify/<id>`.
  Codes are SHA-256 hashed, rate-limited, single-use per send, and can be
  resent (max 3 resends / 5 min). When `BREVO_API_KEY` is not configured the
  app logs a warning and falls back to the old immediate registration (dev and
  test environments are unaffected); when it is configured, delivery failure
  aborts registration (fail-closed).
- `msds/services/brevo.py`: minimal Brevo client (transactional email via REST
  API `BREVO_API_KEY` or SMTP relay `BREVO_SMTP_*` when no API key is set; SMS
  via REST only) using stdlib only; `deliver_secret` (invitation codes,
  temporary passwords) now delivers through Brevo when configured, falling
  back to the previous logging behaviour otherwise.
- Registration email delivery verified end-to-end (live test to the account
  owner). SMS requires the Brevo Transactional SMS addon/credits on the
  account; without it, phone registrations fail closed with a clear message
  directing users to email instead. Brevo IP restriction must list the
  server's public IP (https://app.brevo.com/security/authorised_ips) or every
  request is rejected with 401/525.
- `run_https.py` now actually starts the HTTP->HTTPS 308 redirect on the HTTP
  port (it previously only printed that it would), so browsers on the LAN are
  bounced to the secure site and `getUserMedia` becomes available.
- LAN HTTPS verified end-to-end (TLS handshake 200 on /login; HTTP 308 to
  HTTPS). `python app.py` now warns at startup that plain HTTP blocks the LAN
  camera and points to `run_https.py`.
  Browsers require a secure context for camera access; plain HTTP cannot be
  made to work (Chrome/Edge clients may relax it per-device via
  `chrome://flags/#unsafely-treat-insecure-origin-as-secure`).
- The LAN leaf certificate was re-issued to cover the current LAN IP
  (10.1.24.48); the existing local CA was reused, so installed client CA
  certificates remain valid.
- `instance/brevo.env` (gitignored) holds `BREVO_API_KEY` / sender settings;
  real environment variables take precedence.

### Changed

- `js.camera_unsupported` message now points to the HTTPS URL.

### Notes

- HTTPS port is 5443 by default (`HTTPS_PORT`); 80/5000 redirect to it.
  Install `instance/certs/maize-ca-cert.crt` in each device's trust store or
  accept the browser warning to make the live camera available.
- Production/Docker deployments must set BREVO_API_KEY / BREVO_SENDER_EMAIL /
  BREVO_SENDER_NAME / BREVO_SMS_SENDER as environment variables (instance/ is
  not copied into images).

## 2026-11-05 - Officer scoping repair & role-aware invitations

### Fixed

- Extension officer dashboard showed no diagnoses because the `officer_section`
  mapping was empty and there was no way to assign sections; the admin console
  now has an "Officer Section Assignments" card that lets an administrator tick
  the village sections each officer may see (saved per officer, audited).
  Officers with no sections assigned see a clear warning instead of empty
  charts. The existing officer account was assigned all seven Liwaladzi
  sections to restore pre-SEC-11 visibility.
- The admin invitation form now has an explicit "Invite as" choice
  (Extension Officer / Administrator); the invitation stores the invited role,
  the one-time code is prefixed accordingly (`MUBAS-OFF-` / `MUBAS-ADM-`), the
  activation flow creates the account with the invited role, and the
  invitations table shows the role. Activating an admin invitation works with
  the existing `no_self_registered_admin` trigger because `created_by` is set.
- `officer_invitation.role_id` added (default 2) with a migration in
  `init_db.py`; `maize_schema.sql` updated (fresh installs get the column with
  an FK clause). Existing invitations default to EXTENSION_OFFICER.

## 2026-11-05 - Security & quality remediation (audit response)

Full remediation of the independent security/code audit. The application was
restructured into a `msds` package (app factory + blueprints) and every
High/Medium finding was addressed.

### Security

- Removed the hard-coded administrator password; the first admin is now seeded
  from `MAIZE_ADMIN_*` env vars or a generated password that must be changed on
  first login (SEC-01). `test_api.py` no longer ships default credentials.
- Model is loaded from the safe `.keras` format with `safe_mode=True` and a
  SHA-256 manifest check (SEC-03); class order is read from
  `models/class_mapping.json` and asserted against the model output (SEC-21).
- Rate limiter rewritten: bounded/LRU-pruned, per-account + per-IP keys, ready
  to swap for Redis (SEC-04).
- Password reset is token-based, single-use, hashed and time-limited, with a
  uniform response that does not leak account existence (SEC-06, SEC-13).
- CSRF token rotates on login/password change; logout is POST-only and the idle
  timer submits a CSRF-protected POST (SEC-07).
- Replaced CDN assets with self-hosted Bootstrap/Chart.js and a nonce-based CSP
  with no `unsafe-inline` (SEC-08).
- Evidence images stored per-user/per-day with a retention purge job (SEC-09).
- TLS termination via bundled nginx, non-root container user, `ProxyFix`
  support and HSTS whenever the proxy reports HTTPS (SEC-10, SEC-18).
- Officers are scoped to assigned sections, enforced in SQL (SEC-11).
- Timezone-aware session timeout plus an absolute session lifetime (SEC-12).
- Centralised password policy (12+ chars, mixed classes, common-password check)
  and validated/normalised usernames and contacts (SEC-14, SEC-17).
- Invitation codes and temporary passwords are no longer echoed in the UI;
  delivery is delegated to configurable mail/SMS gateways (SEC-15).
- Audit log is now a tamper-evident HMAC hash chain with an admin "verify"
  action and non-silent failure handling (SEC-16).

### Machine learning

- Dataset split now happens before augmentation, removing train/test leakage
  (SEC-22); split provenance written to `data/split_summary.json`.
- Inference has an entropy/margin-based abstain/OOD path; low-confidence and OOD
  results are not recorded as confirmed disease cases (SEC-20).
- Evaluation persists a versioned metrics report and asserts class order
  (SEC-23).

### Architecture & quality

- Split into `msds/` package: app factory, blueprints (main/auth/farmer/officer/
  admin), services (audit, diagnosis, labels, validation, model registry) and a
  request-scoped WAL database helper (QUA-01, QUA-11).
- Added a pytest suite (27 tests) covering auth, CSRF, lockout, upload
  validation, OOD, officer scoping and audit integrity, plus a GitHub Actions
  CI workflow (QUA-03).
- Pinned runtime dependencies, split dev requirements, removed unused
  `flask-cors`, added `pip-audit` to CI (QUA-04).
- Individual exception handling, typed client errors, structured audit logging
  and a real health/readiness signal (QUA-02, QUA-07, QUA-08).
- Removed stray root artifacts, tightened `.gitignore`/`.dockerignore`
  (QUA-10); rebuilt Docker image to run as non-root with a single model worker.

### Verification

- `pytest tests -q` -> 27 passed.
- All pages render (200/302) with the real database; audit chain verifies.
- `python -m py_compile` on all modules passes.

## 2026-11-04 - Automatic idle-session redirect (`798fca2`)

### Added

- Client-side idle-session timer in `static/js/app.js`.
- Timer reads the server-configured timeout from the page instead of hard-coding it.
- Authenticated pages expose `data-authenticated`, `data-idle-timeout`, and
  `data-logout-url` attributes through `templates/base.html`.
- Cross-tab activity sharing through `localStorage` prevents an idle tab from
  signing out a user who is active in another tab.
- `pageshow` and `visibilitychange` checks handle restored and background tabs.
- Idle expiry uses `window.location.replace()` so the expired page is replaced
  rather than added to browser history.

### Changed

- `/logout?reason=idle` now records an inactivity logout, clears the session,
  displays the session-expired warning, and always redirects to `/login`.
- Manual sign-out still displays the normal signed-out message.
- Authenticated responses now send `Cache-Control: no-store`, `Pragma: no-cache`,
  and `Expires: 0` so the browser back button cannot reopen a protected page.
- `SESSION_IDLE_MINUTES` is injected into every template through the global
  context processor. The default remains 30 minutes.

### Verification

- Python compilation passed.
- JavaScript syntax check passed.
- Server-side timeout test returned `302` to `/login` after 31 minutes of
  simulated inactivity.
- Authenticated dashboard rendered the idle timer attributes.
- Live HTTPS health check returned `200`.
- All 45 smoke tests passed.

## 2026-11-04 - Held-out test dataset (`28ecfb8`)

### Added

- Committed `data/processed/test/` for reproducible model evaluation.
- Dataset size: 288.35 MB, 1,873 images.

| Class | Images | Size |
| --- | ---: | ---: |
| Downy_Mildew | 375 | 52.46 MB |
| Healthy | 748 | 102.50 MB |
| MLN | 375 | 45.72 MB |
| MSV | 375 | 87.67 MB |

### Changed

- Updated `.gitignore` to unignore `data/processed/test/` while keeping the
  larger training and validation sets ignored.

### Not included

- `data/processed/train/` remains local: 1,371.68 MB, 8,720 images.
- `data/processed/val/` remains local: 307.04 MB, 1,874 images.
- Runtime database, uploads, backups, certificates, and private keys remain
  ignored.

## 2026-11-04 - Complete system implementation (`8d57063`)

### Application architecture

- Expanded `app.py` from the initial prototype into the complete application.
- Added configuration for database path, model path, upload folder, upload size,
  confidence threshold, model version, and idle timeout.
- Added one-time model loading at startup and fail-fast behavior when the model
  is unavailable.
- Added SQLite helpers with row access by column name and a 30-second busy
  timeout.
- Added health endpoint, JSON/HTML error handling, and safe redirect validation.
- Added HTTPS startup, automatic certificate generation, and HTTP-to-HTTPS
  redirect support.

### Authentication and account security

- Added farmer self-registration.
- Added sign-in by username, email address, or phone number.
- Added invitation-only extension officer accounts with single-use activation.
- Added administrator account seeding.
- Added administrator user filtering, activation/deactivation, and password reset.
- Added password change with current-password verification and an 8-character
  minimum.
- Added forced temporary-password change before other actions.
- Added five-failure account lockout for 15 minutes.
- Added login rate limiting.
- Added safe session clearing after login to prevent session fixation.
- Added server-side role checks on protected routes.
- Added immediate access revocation for deactivated or removed accounts.
- Added CSRF tokens for state-changing requests.
- Added persistent secret-key loading from `SECRET_KEY` or a local file.
- Added HttpOnly, SameSite=Lax, and optional Secure session cookies.

### Diagnosis workflow and model

- Added image extension and content validation for PNG, JPEG, and JPG.
- Added 5 MB upload limit.
- Added EXIF orientation handling, RGB conversion, resizing, and MobileNetV2
  preprocessing.
- Added four-class prediction: Downy Mildew, Healthy, MLN, and MSV.
- Added confidence threshold and low-confidence warning.
- Added per-class probability persistence.
- Added section assignment and validation.
- Added bilingual advisory lookup by class and language.
- Added diagnosis persistence and model-version recording.
- Added evidence-image storage outside the web root.
- Added diagnosis rate limiting.
- Added authenticated JSON `/predict` endpoint for API clients.
- Added farmer diagnosis history.

### Farmer, officer, and administrator features

- Added farmer diagnostic dashboard with image upload, camera capture, preview,
  and history.
- Added extension officer dashboard with date, class, and section filters.
- Added scan, outbreak, healthy-case, class, weekly-trend, and section summaries.
- Added anonymised CSV report export.
- Added administrator dashboard with user and audit-log filters.
- Added officer invitation creation and revocation.
- Added user status management with protection against removing the last active
  administrator.
- Added temporary-password generation for officer activation and password reset.
- Added audit-log viewing.
- Added account recovery request flow that records a pending administrator action.

### Database, audit, and retention

- Added relational SQLite schema in `maize_schema.sql`.
- Added tables for roles, village sections, disease classes, bilingual
  advisories, event types, users, officer invitations, diagnoses, per-class
  probabilities, and system logs.
- Added foreign keys, uniqueness rules, status checks, and indexes.
- Added append-only audit-log triggers preventing updates and deletes.
- Added triggers enforcing officer invitation and preventing self-registered
  administrators.
- Added seeded roles, disease classes, audit event types, and village sections.
- Added additional audit event types for password change and password reset.
- Added audit events for login, logout, lockout, account changes, diagnosis,
  exports, invitations, CSRF failures, authorization failures, and system errors.

### Internationalisation and interface

- Added `i18n.py` with 251 English and 251 Chichewa keys.
- Added English/Chichewa language switching with session persistence.
- Added translated navigation, forms, flash messages, statuses, roles, classes,
  errors, JavaScript prompts, and advisories.
- Added a responsive Bootstrap-based interface.
- Added `base.html`, login, registration, activation, forgot-password,
  change-password, farmer, officer, administrator, and error templates.
- Removed the original monolithic `templates/index.html`.
- Added `static/css/style.css` for application styling.
- Added translated role, status, and disease-class template filters.
- Added bilingual language names and locale-aware labels.

### Camera, image preparation, and client behaviour

- Added drag-and-drop upload.
- Added image preview.
- Added client-side image resizing to a maximum 1024-pixel side.
- Added JPEG compression targeting 500 KB.
- Added live camera modal using `navigator.mediaDevices.getUserMedia`.
- Added native mobile camera fallback.
- Added camera-denied and unsupported-device messages.
- Added HTTPS-based camera detection instead of relying only on
  `window.isSecureContext`.
- Added muted video playback and camera stream cleanup.
- Added first-use photo guide.
- Added submit progress state and compression guard.

### HTTPS and LAN deployment

- Added `run_https.py` with a local certificate authority.
- Added a 4096-bit RSA CA with SHA-256 and a 10-year validity period.
- Added a 2048-bit RSA server key and CA-signed leaf certificate.
- Added SAN entries for localhost, hostname, loopback, and current LAN IPs.
- Added automatic leaf regeneration when the LAN IP or certificate changes.
- Added a combined leaf-plus-CA chain for TLS serving.
- Added TLS 1.2 minimum version and disabled TLS compression.
- Added HSTS on secure responses.
- Added strict permissions policy allowing only same-origin camera access.
- Added COOP and CORP headers.
- Added `install_ca_windows.ps1` for Windows client trust installation.
- Added automatic HTTP-to-HTTPS redirect on port 5000 to HTTPS on port 5443.
- Added POST-safe 307 redirects.
- Added host allowlisting for redirect targets.
- Added secure-cookie activation when HTTPS is enabled.

### Deployment and operations

- Reworked `Dockerfile` for the completed application.
- Added runtime paths for database, uploads, and backups.
- Added required TensorFlow/Pillow system libraries.
- Added persistent volumes for database, uploads, and backups.
- Added container health check.
- Added Gunicorn startup with two workers and a 120-second timeout.
- Added `docker-entrypoint.sh` with database initialization and daily backup loop.
- Added `backup_db.py` using SQLite's online backup API and 14-backup retention.
- Added file permissions for the CA and server private keys.
- Updated `.gitignore` and `.dockerignore` to exclude runtime data, uploads,
  backups, databases, certificates, and private keys.

### API testing and documentation

- Updated `test_api.py` to sign in with CSRF, fetch a dashboard token, and submit
  an authenticated image prediction.
- Added environment overrides for base URL, username, password, test directory,
  and section.
- Added `Maize_Crop_Disease_System_Implementation_Report.docx`.
- Original report body contains a table of contents and appendices, approximately
  25 pages and 12,312 words, with no plaintext secrets.
- Added Appendix D to the DOCX report, containing this complete change log.
- Added `CHANGELOG.md` to keep the change history versioned in the repository.

### Files introduced or substantially changed

| File | Change |
| --- | --- |
| `app.py` | Complete authenticated application, diagnosis service, dashboards, security, audit, HTTPS startup |
| `i18n.py` | 251 English and 251 Chichewa strings |
| `init_db.py` | Schema execution, villages, advisories, administrator seed |
| `maize_schema.sql` | Normalized schema, constraints, triggers, indexes, reference data |
| `backup_db.py` | Online SQLite backup and retention |
| `run_https.py` | Local CA, leaf certificate, SAN generation, HTTPS launcher |
| `install_ca_windows.ps1` | Windows CA trust installation |
| `docker-entrypoint.sh` | Container initialization and backup loop |
| `Dockerfile` | Production-style container build and health check |
| `test_api.py` | Authenticated CSRF-aware API smoke test |
| `.gitignore` | Runtime and secret exclusions; selected test-data inclusion |
| `.dockerignore` | Runtime and development exclusions |
| `static/css/style.css` | Interface styling |
| `static/js/app.js` | Upload, compression, camera, photo guide, idle logout |
| `templates/base.html` | Shared layout, language menu, security and idle metadata |
| `templates/farmer.html` | Diagnosis form, camera UI, result and history |
| `templates/officer.html` | Filters, summaries, trends and CSV export |
| `templates/admin.html` | Users, invitations, audit log and reset actions |
| `templates/login.html` | Sign-in form |
| `templates/register.html` | Farmer registration |
| `templates/activate.html` | Officer invitation activation |
| `templates/change_password.html` | Password change |
| `templates/forgot_password.html` | Recovery request |
| `templates/error.html` | Error page |
| `templates/index.html` | Removed monolithic prototype page |
| `Maize_Crop_Disease_System_Implementation_Report.docx` | Implementation and audit report |

## 2026-09-29 - Initial repository baseline (`25e94e5`)

Existing at baseline:

- Initial Flask prototype in `app.py`.
- Original single-page `templates/index.html`.
- Trained MobileNetV2 model and evaluation images under `models/`.
- Dataset preparation, training, and evaluation scripts.
- Initial Dockerfile and requirements.
- Initial API test script.
- Initial `.gitignore` and `.dockerignore`.

Changes made after this baseline are documented in the sections above.

## Verification summary

| Check | Result |
| --- | --- |
| Smoke suite | 45/45 passed |
| Python compilation | Passed |
| JavaScript syntax | Passed |
| HTTPS health check with local CA | `200` |
| HTTP-to-HTTPS redirect | `302` to HTTPS |
| TLS 1.2 handshake | Succeeded |
| CA-to-leaf verification | `OK` |
| Idle timeout | Redirects to `/login` |
| i18n key parity | 251 English / 251 Chichewa, no missing keys |
| Test dataset | 1,873 images, 288.35 MB |

## Known limitations and pending work

- `init_db.py` and `test_api.py` still contain the seeded default administrator
  credential; rotate it before any real deployment.
- Docker image build was not validated because the Docker daemon was unavailable.
- The application still uses Flask's development server for the LAN HTTPS
  deployment. A production WSGI server or reverse proxy is recommended for
  public exposure.
- The local CA must be installed on each client. A private LAN IP cannot receive
  a publicly trusted certificate without a public DNS name and ACME setup.
- Chichewa translations have not received independent native-speaker review.
- Latency, concurrency, uptime, backup-restore, and 30-minute backup tests have
  not been fully measured.
- The main body of the DOCX implementation report predates the camera, local-CA,
  dataset, and idle-session changes; Appendix D now records those changes.
- Appendix D was generated from this change log and may need a manual table-of-
  contents refresh when opened in Word.
- The training and validation image sets are intentionally not committed; only
  the held-out test set is versioned.

## Reproduction commands

```powershell
# Compile checks
.\tf_env\Scripts\python.exe -m py_compile app.py run_https.py init_db.py backup_db.py
node --check static\js\app.js

# Start the HTTPS server and HTTP redirect
.\tf_env\Scripts\python.exe run_https.py

# Verify the local CA chain
& 'C:\Program Files\Git\usr\bin\openssl.exe' verify -CAfile instance\certs\maize-ca-cert.pem instance\certs\maize-lan-leaf.pem

# Run the API smoke test
.\tf_env\Scripts\python.exe test_api.py
```
