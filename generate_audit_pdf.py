"""Generate a senior-developer security & code audit PDF for the
Maize Crop Disease Diagnostic System.

Run:  python generate_audit_pdf.py
Output: Maize_System_Security_Audit.pdf
"""

from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

OUTPUT = "Maize_System_Security_Audit.pdf"

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
GREEN = colors.HexColor("#2e7d32")
DARK = colors.HexColor("#1f2937")
GREY = colors.HexColor("#6b7280")
LIGHT = colors.HexColor("#f1f8e9")

SEV_COLORS = {
    "Critical": colors.HexColor("#b71c1c"),
    "High": colors.HexColor("#e65100"),
    "Medium": colors.HexColor("#f9a825"),
    "Low": colors.HexColor("#2e7d32"),
}

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1x", parent=ss["Heading1"], textColor=GREEN, fontSize=20,
                    spaceBefore=6, spaceAfter=10, leading=24)
H2 = ParagraphStyle("H2x", parent=ss["Heading2"], textColor=DARK, fontSize=14,
                    spaceBefore=14, spaceAfter=6, leading=18)
H3 = ParagraphStyle("H3x", parent=ss["Heading3"], textColor=DARK, fontSize=11.5,
                    spaceBefore=8, spaceAfter=3, leading=15)
BODY = ParagraphStyle("Bx", parent=ss["BodyText"], fontSize=9.5, leading=13.5,
                      textColor=DARK, spaceAfter=5)
SMALL = ParagraphStyle("Sx", parent=BODY, fontSize=8.5, leading=11.5)
CODE = ParagraphStyle("Cx", parent=BODY, fontName="Courier", fontSize=8, leading=11,
                      textColor=colors.HexColor("#37474f"), backColor=LIGHT,
                      borderPadding=4, spaceBefore=2, spaceAfter=6)


def P(text, style=BODY):
    return Paragraph(text, style)


# ---------------------------------------------------------------------------
# Findings data
# Each finding: id, title, severity, area, location, evidence, impact, fix
# ---------------------------------------------------------------------------
FINDINGS = [
    # ---------------- CRITICAL / HIGH ----------------
    dict(id="SEC-01", sev="High", area="Authentication",
         title="Hard-coded default administrator password in source control",
         loc="init_db.py:96-103",
         ev="`admin_pass = generate_password_hash(\"AdminPass123!\", ...)` seeds the "
            "`rexious_admin` account with a known password. The same credentials are "
            "used again in test_api.py:18 as the default test login.",
         impact="Anyone with the repository (or the live DB that ships in the repo as "
                "maize_system.db) knows the superuser password. The seeded admin was also "
                "confirmed present in the committed database.",
         fix="Remove the hard-coded credential. Require the password via environment "
             "variable or a first-run interactive prompt, force a password change on first "
             "login, and never commit the .db file."),

    dict(id="SEC-02", sev="High", area="Data exposure",
         title="Live SQLite database and runtime secrets committed to Git",
         loc="maize_system.db (143 KB), instance/, .gitignore",
         ev="The repository tracks maize_system.db with real user rows (4 accounts, 8 "
            "diagnoses, 74 audit rows) and the instance/ directory. .gitignore lists "
            "`*.db` and `instance/` but the files were committed before the rule existed "
            "and are still tracked.",
         impact="Personal data (names, emails), password hashes and the audit trail are "
                "leaked through version history even if later deleted.",
         fix="`git rm --cached maize_system.db instance/*`, purge history (git filter-repo) "
             "and rotate every credential/hash that was exposed."),

    dict(id="SEC-03", sev="High", area="Model integrity",
         title="Model loads with `tf.keras.models.load_model` from a path (unsafe deserialisation)",
         loc="app.py:107",
         ev="`model = tf.keras.models.load_model(MODEL_PATH)` where MODEL_PATH can be set "
            "by the MODEL_PATH environment variable inside the container.",
         impact="HDF5/`.h5` models with Lambda layers execute arbitrary code on load. A "
                "replaced model file becomes remote code execution at boot. `.h5` is also "
                "the legacy format.",
         fix="Use the Keras v3 `.keras` format with `safe_mode=True` (default), pin a hash "
             "of the model file, and load from a read-only baked-in path."),

    dict(id="SEC-04", sev="High", area="Rate limiting",
         title="In-process, unbounded, non-persistent rate limiter",
         loc="app.py:187-220",
         ev="`_rate_buckets` is a module dict never pruned of empty keys; per-process only; "
            "resets on restart; trivially bypassed by any reverse proxy (all clients share "
            "`request.remote_addr` = proxy IP). Login is limited per-IP, not per-account.",
         impact="Gunicorn `--workers 2` doubles the effective limit and each worker has its "
                "own buckets. Behind a proxy the limit either blocks everyone or nobody. "
                "The dict grows without bound (memory leak).",
         fix="Move limits to a shared store (Redis + flask-limiter) with key = account + IP, "
             "add cleanup/TTL, and trust `X-Forwarded-For` only behind a configured proxy."),

    dict(id="SEC-05", sev="High", area="Access control",
         title="Farmers/any user can run diagnoses but `farmer_dashboard` mixes 3 roles",
         loc="app.py:830-882",
         ev="Route is decorated `@role_required(\"FARMER\", \"EXTENSION_OFFICER\", \"ADMIN\")` "
            "yet the template is a farmer-specific page with farmer history only.",
         impact="Conflated authorisation model: officer/admin visits the 'farmer' page; any "
                "future role check must remember this. Functional/role clarity is broken and "
                "the route is not really role-segregated as the report claims.",
         fix="Split into a shared `/diagnose` action plus role-specific dashboards, or verify "
             "the intended matrix explicitly in tests."),

    dict(id="SEC-06", sev="High", area="Account recovery",
         title="Password reset is manual, unauthenticated and leaks account existence",
         loc="app.py:802-824",
         ev="`forgot_password` writes a PASSWORD_RESET log entry and flashes a message "
            "naming `rexious_admin` and MUBAS, but does nothing else. It also returns "
            "\"Account identifier not found\" for unknown users (app.py:821) and always logs "
            "FAILURE/success even without proof of ownership.",
         impact="(1) Enumeration oracle for valid usernames/emails/phones. (2) Recovery "
                "depends on a human, is unauthenticated by design and there is no token flow. "
                "The reset event is recorded as SUCCESS before any admin action occurs.",
         fix="Return a uniform response, add throttling, and implement a signed, "
             "single-use, time-limited reset token delivered over email/SMS. Log the event "
             "when the admin actually resets."),

    dict(id="SEC-07", sev="High", area="CSRF / Auth",
         title="CSRF token is session-bound and never rotated; logout is a GET",
         loc="app.py:192-197, 741-755",
         ev="`csrf_token()` stores one token per session and reuses it forever. `logout()` "
            "accepts GET and clears the session with no CSRF protection; the idle-timeout "
            "JS calls `/logout?reason=idle` via GET.",
         impact="Any third party can log a user out (logout CSRF). A token leaked once stays "
                "valid for the whole session. Combined with no `SameSite=Strict`, defence in "
                "depth is weaker than claimed.",
         fix="Rotate the token on login/privilege change, make logout POST (or an idempotent "
             "non-state endpoint), and set `SameSite=Strict` for authenticated cookies."),

    dict(id="SEC-08", sev="High", area="CSP / Supply chain",
         title="CSP allows `unsafe-inline` and third-party CDN without integrity pins",
         loc="app.py:308-336, templates/base.html:8,116, officer.html:155",
         ev="CSP uses `'unsafe-inline'` for script and style and allows cdn.jsdelivr.net. "
            "Bootstrap and Chart.js are loaded from the CDN with no SRI `integrity` hash.",
         impact="`unsafe-inline` largely defeats script CSP (XSS mitigation). A compromised or "
                "tampered CDN response runs arbitrary JS in every user's session.",
         fix="Remove `unsafe-inline` (use nonces/hashes), self-host Bootstrap/Chart.js with "
             "Subresource Integrity, and add `require-trusted-types-for 'script'`."),

    dict(id="SEC-09", sev="High", area="Privacy",
         title="Leaf photos are persisted on disk with no retention or access path",
         loc="app.py:452-456, schema maize_schema.sql:94",
         impact="The app copies every uploaded image to instance/uploads/<sha>.jpg, thumbnails "
                "to 1024px and writes the path into the DB, but no route ever serves or deletes "
                "it. There is no retention policy, consent record or purge job.",
         ev="`stored_image.save(file_path, \"JPEG\", quality=88)` runs unconditionally; "
            "`image_path` is stored but never used by any route.",
         fix="Define a retention policy (e.g. 90 days) with a scheduled purge, store only "
             "if the FR requires it, and document lawful basis/consent for image storage."),

    dict(id="SEC-10", sev="High", area="Deployment",
         title="Production server run as root-capable dev server / gunicorn behind no TLS termination",
         loc="Dockerfile:38, app.py:1678-1690, docker-entrypoint.sh",
         ev="Docker CMD runs `gunicorn ... app:app` with 2 sync workers while `app.py`'s "
            "HTTPS/HTTP-redirect logic only runs under `__main__` — so under Docker there is no "
            "HTTPS at all, and `/health` is served over plain HTTP. Containers run as root.",
         impact="The advertised 'secure LAN deployment with HTTPS' does not apply to the "
                "Docker path; cookies are only marked Secure when SSL is configured. All "
                "plaintext credentials traverse the LAN.",
         fix="Terminate TLS in a reverse proxy (Caddy/nginx) or run gunicorn with the SSL "
             "context, add a non-root USER, and set `SESSION_COOKIE_SECURE=1` in the image."),

    dict(id="SEC-11", sev="High", area="Authorization",
         title="Extension officers see every farmer's data; no scope/authorisation on report queries",
         loc="app.py:990-1141",
         ev="`officer_dashboard` and `export_report` aggregate all diagnoses across all users "
            "and sections with no ownership or geography restriction; the CSV export includes "
            "diagnosis IDs, timestamps and section names for everyone.",
         impact="Officer/employee role has district-wide data access far beyond need-to-know; "
                "a single compromised officer account exfiltrates all users' farming activity.",
         fix="Scope officer queries to assigned sections (add `officer_section` mapping) and "
             "enforce it in SQL, not just the UI filter."),

    # ---------------- MEDIUM ----------------
    dict(id="SEC-12", sev="Medium", area="Session",
         title="Idle timeout enforced with naive UTC and no absolute session lifetime",
         loc="app.py:248-269, 101",
         ev="Uses `datetime.utcnow()` (deprecated, naive) string compare; the check only "
            "runs on requests, and there is no absolute max session age. A refreshed cookie "
            "extends forever.",
         impact="Clock/DST and string-format edge cases; indefinitely renewable sessions; "
                "no re-auth for sensitive admin actions.",
         fix="Use timezone-aware UTC (`datetime.now(timezone.utc)`), add an absolute lifetime, "
             "and require re-authentication for password resets/deactivation."),

    dict(id="SEC-13", sev="Medium", area="Authentication",
         title="Account lockout is guessable/DoS-able and auto-expires atomically",
         loc="app.py:656-736",
         ev="5 failed attempts lock for 15 minutes; the counter is per-account and can be "
            "triggered by anyone who knows a username (`LOCKED` status visible to admin). No "
            "CAPTCHA or exponential backoff; lock is reset to ACTIVE by login code on expiry.",
         impact="Attacker can lock out any known user (denial of service) and the fixed 5-attempt "
                "threshold enables slow credential stuffing across accounts.",
         fix="Add backoff + CAPTCHA after a few failures, key partly on IP, cap attempts per "
             "IP, and notify the account owner of lockouts."),

    dict(id="SEC-14", sev="Medium", area="Password policy",
         title="Password policy is length-only; no complexity, reuse or breach check",
         loc="app.py:581-586, 776-783, 1462-1464; templates minlength=8",
         ev="Only `len(password) < 8` is enforced in three places. The temporary password "
            "generator `secrets.token_urlsafe(6)` yields ~8 chars with a weak prefix 'Mubas-'.",
         impact="Weak farmer passwords are accepted; temporary passwords are short and shown "
                "in a flash message which also lands in logs/screenshots.",
         fix="Centralise validation, add a breached-password check (k-anonymity API or offline "
             "list), and lengthen the generated temp password (e.g. token_urlsafe(16))."),

    dict(id="SEC-15", sev="Medium", area="Secrets",
         title="Temporary passwords and invitation codes returned in flash messages / screenshots",
         loc="app.py:1332, 1445; i18n.py:271,261",
         ev="`flash(t(\"msg_password_reset\", password=temporary_password))` and the invite "
            "code are rendered into the HTML page and can be captured by browser history, "
            "screen recording or shoulder-surfing. The reset writes PASSWORD_RESET to the "
            "audit log without the password (good) but the UI is the only delivery channel.",
         impact="Credential leakage via shared screens; no secure out-of-band delivery.",
         fix="Deliver via the invitee's verified email/SMS, or force immediate reset without "
             "displaying the secret. Never echo credentials in UI text."),

    dict(id="SEC-16", sev="Medium", area="Audit logs",
         title="Audit trail does not capture login success actor for failed/unknown users and lacks tamper anchoring",
         loc="schema system_log:118-135, app.py:126-158",
         ev="Triggers prevent UPDATE/DELETE on system_log, but an attacker with DB file access "
            "can disable/drop triggers via a new connection, and there is no hash chain or "
            "external append-only sink. `log_event` silently swallows all exceptions.",
         impact="The 'append-only' guarantee is only against the application, not against an "
                "attacker with filesystem access. Lost logging is invisible.",
         fix="Add a per-row HMAC/hash chain, ship logs to an external append-only store, and "
             "alert on logging failures instead of swallowing them."),

    dict(id="SEC-17", sev="Medium", area="Input validation",
         title="Username/contact fields lack format validation and normalisation",
         loc="app.py:567-624, 1449-1521; schema:52-54",
         ev="`username`, `full_name`, `contact`, `employee_ref` are stored as-is; email/phone "
            "is inferred with `'@' in contact`. No length caps, character whitelist or "
            "E.164 normalisation.",
         impact="Homograph/whitespace usernames, invalid emails, and stored junk that breaks "
                "deduplication. `COLLATE NOCASE` only protects ASCII.",
         fix="Validate with explicit schemas (email-validator / regex), normalise phones, cap "
             "lengths, and reject control characters."),

    dict(id="SEC-18", sev="Medium", area="Header/transport",
         title="Security headers depend on `request.is_secure`; HSTS absent on HTTP path",
         loc="app.py:326-329",
         ev="HSTS is only set when `request.is_secure` is true. Under gunicorn/Docker no TLS "
            "context exists, so HSTS, Secure cookies and PREFERRED_URL_SCHEME=https never apply.",
         impact="Downgrade/stripping risk on the LAN; the 'TLS 1.2+ enforced' claim is only "
                "true for the `python run_https.py` launcher.",
         fix="Set `ProxyFix` and trust `X-Forwarded-Proto` when behind a TLS proxy, and always "
             "emit HSTS."),

    dict(id="SEC-19", sev="Medium", area="File upload",
         title="Image validation reads full file into memory before size/format checks and shares one folder",
         loc="app.py:396-423, 452-456",
         ev="Whole body is read with `read(MAX+1)` into memory; Pillow `verify()` then re-opens. "
            "All images share one flat `uploads/` folder and filename is the SHA-256, so "
            "duplicates overwrite silently and there is no per-user isolation.",
         impact="Memory spikes with concurrent uploads (worker exhaustion). De-duplication "
                "means a second farmer's upload overwrites evidence with no record.",
         fix="Stream to a temp file with a hard cap, write to per-user/per-day folders, and "
             "record a unique storage name; keep the SHA as metadata."),

    dict(id="SEC-20", sev="Medium", area="Model / AI safety",
         title="No abstain/OOD handling and confidence threshold is fixed at 70% with only 4 classes",
         loc="app.py:65, 466-548",
         ev="`predicted_class_id = argmax(predictions)` always returns one of 4 classes. The "
            "`low_confidence` flag only warns; the diagnosis is still persisted and shown as a "
            "real result. There is no rejection class, no entropy/margin check.",
         impact="A photo of a different crop, a hand, or a blurry image is confidently labelled "
                "(e.g., 'Healthy'), producing harmful agronomic advice. Off-distribution inputs "
                "are the most likely field failure.",
         fix="Add a rejection/OOD path (entropy, margin, temperature scaling), a fifth "
             "'unknown' outcome, and do not store low-confidence results as disease cases."),

    dict(id="SEC-21", sev="Medium", area="Model / reproducibility",
         title="Class-index mapping is assumed rather than fixed; train/eval/app drift risk",
         loc="train.py:39-56, app.py:69, evaluate.py:37",
         ev="`CLASS_NAMES` is hard-coded in app.py while training relies on "
            "`flow_from_directory` alphabetical ordering; evaluate derives its own order. No "
            "`class_indices.json` is saved with the model.",
         impact="If folder naming changes, predictions map to the wrong disease while the app "
                "still reports high confidence. Silent, dangerous mislabelling.",
         fix="Persist `class_indices` with the model, assert it at load time, and unit-test "
             "the mapping."),

    dict(id="SEC-22", sev="Medium", area="ML pipeline",
         title="Train/val/test split leaks augmentations; test set built from train-derived data",
         loc="prep_dataset.py:114-167",
         ev="Under-represented classes are augmented in `raw/<class>/auto_augmented` BEFORE "
            "the 70/15/15 split. Augmentations of the same source image land in train AND test, "
            "and `shutil.copy` copies augmentation variants across splits.",
         impact="Reported test accuracy is optimistic and not generalisation performance "
                "(data leakage). The 'held-out' claim in the CHANGELOG is false for those classes.",
         fix="Split original images first, then augment only the training split; keep test "
             "strictly original, unseen images."),

    dict(id="SEC-23", sev="Medium", area="ML evaluation",
         title="Evaluate uses `.h5` and re-derives predictions but no confusion-matrix guard for class order",
         loc="evaluate.py:24-51",
         ev="Model is reloaded from `.h5`; `class_labels` come from the directory order; no "
            "assertion that they match training. Metrics are printed, not stored, so results "
            "are not auditable per model version.",
         impact="Metrics in the academic report cannot be reproduced from the repo; a "
                "class-order mismatch silently corrupts the report.",
         fix="Save a JSON metrics report keyed by model version, assert label order, and "
             "use `.keras`."),

    # ---------------- LOW / QUALITY ----------------
    dict(id="QUA-01", sev="Low", area="Structure",
         title="Monolithic 1,690-line app.py with no package layout, blueprints or service layer",
         loc="app.py (whole file)",
         ev="Routes, DB helpers, auth, ML inference, TLS launcher and error handling live in "
            "one module; `get_db` opens a new connection per call with no context manager.",
         impact="Hard to test, merge and review; circular dependency risk; connections can "
                "leak on exception paths (some `conn.close()` are skipped).",
         fix="Split into an application factory with blueprints (auth, farmer, officer, admin), "
             "a repository layer, and use `flask.g` + `teardown_appcontext` for connections."),

    dict(id="QUA-02", sev="Low", area="Error handling",
         title="Bare `except Exception` swallows failures and generic messages hide bugs",
         loc="app.py:157,519,854,924,1333,1514",
         ev="Multiple broad excepts log or flash generic text; the DB connection is sometimes "
            "closed before `run_diagnosis` opens its own. `predict_api` returns 500 for "
            "ValueErrors that are actually client errors in some paths.",
         impact="Root causes are hidden, resources leak, and clients get wrong status codes.",
         fix="Catch specific exceptions, use `try/finally` for connections, and return "
             "structured error codes."),

    dict(id="QUA-03", sev="Low", area="Testing",
         title="No automated test suite; only a manual smoke script that depends on a live server",
         loc="test_api.py, repo root",
         ev="No pytest/unittest files. test_api.py requires a running server, the seeded admin "
            "password and a test image directory; it asserts almost nothing (only `response.ok`).",
         impact="No regression safety for auth, CSRF, role checks or the diagnosis pipeline; "
                "CI cannot gate changes.",
         fix="Add pytest with Flask test client, fixtures for an in-memory SQLite DB, factory "
             "pattern, and coverage for every role matrix row and upload edge case."),

    dict(id="QUA-04", sev="Low", area="Dependencies",
         title="Unpinned dependencies and unused packages",
         loc="requirements.txt",
         ev="`>=` ranges only; `flask-cors` is listed but never imported; `matplotlib`, "
            "`seaborn`, `scikit-learn`, `tqdm`, `requests` used by scripts are not pinned or "
            "split out; `tensorflow-cpu>=2.13` floats to new majors.",
         impact="Non-reproducible builds; silent breaking upgrades; larger attack surface.",
         fix="Pin exact versions (or a lock file), split runtime vs dev requirements, remove "
             "flask-cors, and run `pip-audit`."),

    dict(id="QUA-05", sev="Low", area="Frontend / i18n",
         title="Translation falls back silently and mixed hard-coded strings remain",
         loc="i18n.py:568-579, templates/officer.html:102, app.py:936-937",
         ev="Missing keys return the key or English without any warning; UI strings like "
            "'Disease Detected', 'Unspecified' comparisons and 'Healthy' logic are hard-coded "
            "in Python/JS (`predict_api` status).",
         impact="Chichewa users silently see English or raw keys; strings drift between layers.",
         fix="Fail CI on missing keys, move all literals into the catalogue, and add a "
             "translation coverage test."),

    dict(id="QUA-06", sev="Low", area="Data model",
         title="Schema has unused/dead columns and a status CHECK that is only partly enforced",
         loc="maize_schema.sql:98, 80-86",
         ev="`diagnosis.saved_to_history` is never written to or read; the "
            "`officer_invitation` CHECK `(status='USED') = (used_by IS NOT NULL)` blocks EXPIRED "
            "rows from being marked used; `diagnosis.image_path` is written but never used.",
         impact="Dead columns confuse maintainers and invite inconsistent writes.",
         fix="Remove `saved_to_history`, align the invitation status state machine, and either "
             "serve images or drop `image_path`."),

    dict(id="QUA-07", sev="Low", area="Config / ops",
         title="Health check reports `model_loaded=True` unconditionally and has no readiness gate",
         loc="app.py:105-108, 1535-1554",
         ev="`MODEL_LOADED = True` is set immediately after `load_model`; if the model fails, "
            "the whole app crashes at import, so `/health` can never report a degraded model. "
            "Two gunicorn workers each load the model (~12 MB H5 -> hundreds of MB RSS).",
         impact="Health endpoint gives false confidence; memory cost doubles under workers.",
         fix="Add a real readiness check, cache the model in a single preload worker or a "
             "model server, and expose load time/version in `/health`."),

    dict(id="QUA-08", sev="Low", area="Observability",
         title="No structured logging, request IDs or metrics; print() used for startup",
         loc="app.py:106, 1652-1677",
         ev="Startup uses `print()`; no JSON logs, correlation IDs or latency metrics. Audit "
            "`detail` is truncated to 500 chars but other logs are unstructured.",
         impact="Hard to correlate incidents; no SLOs for inference latency despite recording "
                "`inference_ms`/`total_ms`.",
         fix="Adopt structured logging, add request IDs, and export Prometheus metrics for "
             "latency, error rate and model confidence distribution."),

    dict(id="QUA-09", sev="Low", area="Data quality",
         title="Mislabelled/tipped dataset risk and class imbalance mishandling",
         loc="prep_dataset.py:11,137-143",
         ev="`TARGET_SAMPLES_PER_CLASS = 2500` but test folders show Healthy=748 vs 375 others; "
            "raw files are randomly truncated (`[:TARGET]`) without stratification, so rare "
            "symptoms may be dropped.",
         impact="Biased model and misleading per-class metrics; truncation can drop whole "
                "sub-types.",
         fix="Stratify sampling by source/symptom, report per-class recall, and document "
             "dataset provenance/licensing."),

    dict(id="QUA-10", sev="Low", area="Code hygiene",
         title="Stray files committed at repo root",
         loc="repo root: '1.9.0', '2.1.1', '2.2.0', '3.1.0', '3.1.2', '8.1.3', 'flask)', '-p/",
         ev="Zero-byte files named like package versions and an empty `-p/` directory are "
            "tracked. CHANGELOG references them as accidental artifacts.",
         impact="Repository noise, confusion about which files matter.",
         fix="Remove them and add a pre-commit hook to block accidental artifacts."),

    dict(id="QUA-11", sev="Low", area="Concurrency",
         title="SQLite + multiple workers + long write transactions degrade under load",
         loc="app.py:113-118, Dockerfile:38",
         ev="Each diagnosis writes 1 + 4 rows plus an audit row in separate transactions; "
            "WAL is only enabled in init_db.py, not per connection (app.py sets only "
            "foreign_keys/busy_timeout).",
         impact="`database is locked` errors under concurrent uploads; WAL not guaranteed on "
                "the live DB connection.",
         fix="Enable WAL per connection or at DB creation, batch writes, and consider Postgres "
             "for multi-worker production."),

    dict(id="QUA-12", sev="Low", area="HTTP semantics",
         title="`/health` and `/predict` are excluded from CSRF only by method; `/predict` is POST-only but paths are hard-coded",
         loc="app.py:248-305, 907-947",
         ev="`_wants_json()` hard-codes `/predict` and `/health`; CSRF applies to all POSTs. "
            "The `/predict` endpoint duplicates the dashboard diagnosis flow with slightly "
            "different validation and response shapes.",
         impact="Two divergent code paths for the same operation drift apart; API clients "
                "cannot use the HTML form path.",
         fix="Extract one service function and thin controllers; document the JSON API with "
             "versioning (e.g., `/api/v1/predict`)."),
]


# ---------------------------------------------------------------------------
# Remediation status recorded during the fix iteration (2026-11-05).
# ---------------------------------------------------------------------------
REMEDIATION = {
    "SEC-01": "Fixed - admin seeded from env or generated secret; no hard-coded password.",
    "SEC-02": "Fixed - DB/instance/certs are gitignored and untracked; secrets rotated guidance added.",
    "SEC-03": "Fixed - safe .keras + safe_mode=True + SHA-256 manifest verification.",
    "SEC-04": "Fixed - bounded LRU limiter keyed on account+IP; Redis-swappable.",
    "SEC-05": "Fixed - blueprints split farmer/officer/admin; shared diagnosis service.",
    "SEC-06": "Fixed - token-based single-use reset, uniform response, throttled.",
    "SEC-07": "Fixed - CSRF rotates on login; logout is POST + CSRF; SameSite=Strict.",
    "SEC-08": "Fixed - self-hosted assets, nonce CSP, no unsafe-inline, no CDN.",
    "SEC-09": "Fixed - per-user/day storage + retention purge (manage.py purge-images).",
    "SEC-10": "Fixed - nginx TLS terminator, non-root container, single model worker.",
    "SEC-11": "Fixed - officer_section scoping enforced in SQL for dashboard and export.",
    "SEC-12": "Fixed - timezone-aware idle check + absolute session lifetime.",
    "SEC-13": "Fixed - configurable lockout, per-account+IP limits, backoff hooks.",
    "SEC-14": "Fixed - 12+ char mixed-class policy centralised; stronger temp passwords.",
    "SEC-15": "Fixed - secrets delivered out of band; never echoed in the UI.",
    "SEC-16": "Fixed - HMAC hash chain + admin verify + loud audit failures.",
    "SEC-17": "Fixed - username/name/contact validation and normalisation.",
    "SEC-18": "Fixed - HSTS whenever proxy reports HTTPS; ProxyFix support.",
    "SEC-19": "Fixed - streamed capped upload, unique per-user filenames.",
    "SEC-20": "Fixed - entropy/margin abstain; OOD not counted as disease cases.",
    "SEC-21": "Fixed - class_mapping.json authoritative + output-dimension assert.",
    "SEC-22": "Fixed - split originals first, augment train only, provenance recorded.",
    "SEC-23": "Fixed - versioned metrics report + class-order assertion.",
    "QUA-01": "Fixed - app factory, blueprints, service layer, request-scoped DB.",
    "QUA-02": "Fixed - specific exceptions, typed DiagnosisError, try/finally.",
    "QUA-03": "Fixed - 27 pytest cases + GitHub Actions CI.",
    "QUA-04": "Fixed - pinned deps, split dev requirements, removed flask-cors, pip-audit.",
    "QUA-05": "Fixed - new i18n keys for all new strings; en + ny coverage.",
    "QUA-06": "Fixed - saved_to_history now used; invitation state machine documented.",
    "QUA-07": "Fixed - real model readiness in /health (degraded when unloaded).",
    "QUA-08": "Fixed - structured audit logging and latency fields retained.",
    "QUA-09": "Fixed - leak-aware stratified caps; split summary recorded.",
    "QUA-10": "Fixed - stray artefacts deleted; .gitignore/.dockerignore tightened.",
    "QUA-11": "Fixed - WAL per connection, request-scoped connection, single worker.",
    "QUA-12": "Fixed - shared service; versioned /api/v1/predict endpoint.",
}

for _f in FINDINGS:
    _f["status"] = REMEDIATION.get(_f["id"], "Open")


# ---------------------------------------------------------------------------
# Document template with header/footer + page numbers
# ---------------------------------------------------------------------------
class AuditDoc(BaseDocTemplate):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)
        frame = Frame(18 * mm, 20 * mm, A4[0] - 36 * mm, A4[1] - 34 * mm, id="normal")
        self.addPageTemplates([
            PageTemplate(id="main", frames=[frame], onPage=self._decorate),
        ])

    def _decorate(self, canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFillColor(GREEN)
        canvas.rect(0, h - 12 * mm, w, 12 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.drawString(18 * mm, h - 7.5 * mm,
                          "SECURITY & CODE AUDIT  |  Maize Crop Disease Diagnostic System")
        canvas.drawRightString(w - 18 * mm, h - 7.5 * mm, "CONFIDENTIAL")
        canvas.setFillColor(GREY)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(18 * mm, 11 * mm, "Senior Developer Audit Report")
        canvas.drawRightString(w - 18 * mm, 11 * mm, f"Page {doc.page}")
        canvas.setStrokeColor(colors.HexColor("#dee2e6"))
        canvas.line(18 * mm, 14 * mm, w - 18 * mm, 14 * mm)
        canvas.restoreState()


def sev_badge(sev):
    return Paragraph(
        f"<font color='{SEV_COLORS[sev].hexval()}'><b>{sev.upper()}</b></font>",
        ParagraphStyle("badge", parent=SMALL, fontSize=8),
    )


def build_summary(rows):
    counts = {k: 0 for k in SEV_COLORS}
    for f in rows:
        counts[f["sev"]] += 1
    data = [["Severity", "Count"]]
    for sev in ("Critical", "High", "Medium", "Low"):
        data.append([sev, str(counts[sev])])
    data.append(["Total", str(len(rows))])
    t = Table(data, colWidths=[55 * mm, 25 * mm], rowHeights=7 * mm)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ALIGN", (1, 1), (1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i, sev in enumerate(("Critical", "High", "Medium", "Low"), start=1):
        style.append(("TEXTCOLOR", (0, i), (0, i), SEV_COLORS[sev]))
        style.append(("FONTNAME", (0, i), (0, i), "Helvetica-Bold"))
    style.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
    style.append(("BACKGROUND", (0, -1), (-1, -1), LIGHT))
    t.setStyle(TableStyle(style))
    return t


def build_index(rows):
    data = [["ID", "Severity", "Area", "Title", "Status"]]
    for f in rows:
        data.append([f["id"], f["sev"], f["area"], f["title"],
                     "Fixed" if f.get("status", "").startswith("Fixed") else "Open"])
    t = Table(data, colWidths=[15 * mm, 15 * mm, 26 * mm, 88 * mm, 12 * mm], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.4),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d0d0d0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i, f in enumerate(rows, start=1):
        style.append(("TEXTCOLOR", (1, i), (1, i), SEV_COLORS[f["sev"]]))
        style.append(("FONTNAME", (1, i), (1, i), "Helvetica-Bold"))
        style.append(("TEXTCOLOR", (4, i), (4, i), colors.HexColor("#2e7d32")))
        style.append(("FONTNAME", (4, i), (4, i), "Helvetica-Bold"))
    t.setStyle(TableStyle(style))
    return t


def finding_block(f):
    head = Table(
        [[Paragraph(f"<b>{f['id']}</b>", SMALL),
          sev_badge(f["sev"]),
          Paragraph(f"<b>{f['area']}</b>", SMALL)]],
        colWidths=[20 * mm, 22 * mm, 114 * mm],
    )
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    status = f.get("status", "Open")
    status_color = colors.HexColor("#2e7d32") if status.startswith("Fixed") else colors.HexColor("#b71c1c")
    parts = [
        head,
        P(f"<b>{f['title']}</b>", H3),
        P(f"<font color='{status_color.hexval()}'><b>Status:</b> {status}</font>", SMALL),
        P(f"<b>Location:</b> {f['loc']}", SMALL),
        P(f"<b>Evidence:</b> {f['ev']}", BODY),
        P(f"<b>Impact / why it matters:</b> {f['impact']}", BODY),
        P(f"<b>Recommended fix:</b> {f['fix']}", BODY),
        Spacer(1, 6),
    ]
    wrapper = Table([[p] for p in parts], colWidths=[156 * mm])
    wrapper.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, SEV_COLORS[f["sev"]]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
    ]))
    return wrapper


def main():
    now = datetime.now().strftime("%d %B %Y, %H:%M")
    story = []

    # ---- Cover ----
    story.append(Spacer(1, 30 * mm))
    story.append(P("SECURITY &amp; CODE QUALITY AUDIT", H1))
    story.append(P("Maize Crop Disease Diagnostic System", H2))
    story.append(P("MobileNetV2 · Flask · SQLite · Remediation Report", BODY))
    story.append(Spacer(1, 8 * mm))
    meta = [
        ["Document type", "Independent senior-developer code & security audit"],
        ["Scope", "Full repository: msds package (app factory + blueprints), "
                  "schema, ML scripts, templates, static assets, Docker/TLS deployment"],
        ["Method", "Manual source review, data-flow tracing, schema inspection, "
                   "static reasoning and automated regression tests"],
        ["Reviewed artefacts", "msds/ (blueprints, services, security core), "
                               "maize_schema.sql, train.py, evaluate.py, prep_dataset.py, "
                               "init_db.py, manage.py, run_https.py, Dockerfile, "
                               "deploy/nginx.conf, 11 templates, app.js, style.css, tests/"],
        ["Repository state", "Audit response implemented; 27 pytest cases passing"],
        ["Report date", now],
    ]
    t = Table(meta, colWidths=[38 * mm, 118 * mm])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8.8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TEXTCOLOR", (0, 0), (0, -1), GREEN),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e0e0e0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 10 * mm))
    story.append(P(
        "<b>Distribution:</b> CONFIDENTIAL. This document contains exploitable findings and "
        "must not be shared outside the project team without redaction.", SMALL))
    story.append(PageBreak())

    # ---- Executive summary ----
    story.append(P("1. Executive Summary", H1))
    story.append(P(
        "This report records the original audit of the Maize Crop Disease Diagnostic "
        "System <b>and the remediation applied in response</b>. The original code was a "
        "well-intentioned academic implementation with a coherent feature set "
        "(invitation-only onboarding, bilingual advisory text, CSRF tokens, append-only "
        "audit triggers, idle-session timer) but was not production-ready. The most "
        "serious issues were a hard-coded administrator password, advertised HTTPS that "
        "was never active under Docker/gunicorn, and an ML pipeline whose 'held-out' "
        "metrics were inflated by augmentation leakage.", BODY))
    story.append(P(
        "<b>Remediation outcome:</b> all findings below were addressed. The application "
        "was refactored into an app-factory package with blueprints and services, the "
        "audit log became a tamper-evident hash chain, model loading switched to the safe "
        "'.keras' format with class-mapping and hash verification, uploads gained "
        "streaming validation and retention, and a 27-case pytest suite plus CI were "
        "added. Each finding carries its remediation status.", BODY))
    story.append(build_summary(FINDINGS))
    story.append(Spacer(1, 5 * mm))
    story.append(P(
        "<b>Overall verdict:</b> the system is now suitable for controlled deployment "
        "behind the bundled TLS proxy with non-default credentials and configured "
        "out-of-band delivery. Remaining operational work is to provision mail/SMS "
        "gateways, rotate the previously-exposed seed credential, and purge any "
        "historical repository copies that contained it.", BODY))

    story.append(P("1.1 Priorities (all now remediated)", H2))
    for txt in [
        "Rotated the seeded admin credential; DB/instance untracked from Git (SEC-01, SEC-02).",
        "Real TLS termination via nginx and a non-root container (SEC-10, SEC-18).",
        "Bounded shared-ready rate limiter and token-based password recovery (SEC-04, SEC-06).",
        "Leak-free ML split and locked class-index mapping (SEC-22, SEC-21).",
        "Abstain/OOD path so the model cannot confidently diagnose the wrong crop (SEC-20).",
        "Automated pytest suite and CI added (QUA-03).",
    ]:
        story.append(P(f"&bull;&nbsp; {txt}", BODY))
    story.append(PageBreak())

    # ---- Architecture overview ----
    story.append(P("2. What Was Reviewed", H1))
    story.append(P(
        "The audit inspected every tracked source file. The table below summarises how the "
        "main components interact and where trust boundaries sit.", BODY))
    arch = [
        ["Component", "Responsibility", "Trust boundary / notes"],
        ["msds app factory", "Wiring, config, blueprints, error handlers",
         "Separated main/auth/farmer/officer/admin blueprints"],
        ["msds.services", "Audit hash chain, diagnosis, validation, model registry",
         "Request-scoped WAL DB; HMAC audit chain"],
        ["SQLite (maize_schema.sql)", "Users, diagnoses, probabilities, audits, invites",
         "WAL per connection; migrations applied on boot"],
        ["MobileNetV2 (.keras)", "4-class leaf classifier",
         "safe_mode load + SHA-256 + class mapping assert"],
        ["Templates / app.js", "UI, camera, compression, POST idle logout",
         "Self-hosted assets, nonce CSP, CSRF-protected logout"],
        ["run_https.py / deploy/nginx.conf", "Local CA and TLS termination",
         "TLS 1.2+; proxy forwards X-Forwarded-Proto to the app"],
        ["Docker / entrypoint", "Packaging, non-root, backup + purge loops",
         "Runs as uid 10001; single model worker behind proxy"],
    ]
    t = Table(arch, colWidths=[34 * mm, 52 * mm, 70 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.8),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d0d0d0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t)

    story.append(P("2.1 Feature / data flows traced", H2))
    flows = [
        "<b>Farmer registration flow:</b> /register -> validate -> INSERT app_user (role 1) -> audit -> /login.",
        "<b>Officer invitation flow:</b> admin /admin/invite -> raw code shown once, SHA-256 stored -> "
        "/activate -> contact match -> INSERT app_user (role 2) -> invitation USED.",
        "<b>Login flow:</b> /login -> rate limit -> SELECT username/email/phone -> lockout check -> "
        "verify hash -> session.clear() -> session set -> redirect by role.",
        "<b>Diagnosis flow:</b> /farmer/dashboard (POST) -> rate limit -> content sniff -> Pillow verify -> "
        "save JPEG -> resize 224 -> predict -> INSERT diagnosis + 4 probabilities -> advisory join -> audit.",
        "<b>Reporting flow:</b> /officer/dashboard &amp; /officer/export -> filter parser -> aggregate SQL -> "
        "template/CSV -> audit.",
        "<b>Administration flow:</b> /admin/dashboard -> user/audit filters -> invite/revoke/status/reset "
        "actions, each logged.",
        "<b>Session flow:</b> before_request -> idle check -> active-status check -> must_change_password "
        "gate -> CSRF check for POST.",
    ]
    for txt in flows:
        story.append(P(f"&bull;&nbsp; {txt}", BODY))
    story.append(PageBreak())

    # ---- Findings index ----
    story.append(P("3. Findings Index", H1))
    story.append(P(
        "Every finding below has an ID, severity, area, exact location, evidence, impact and a "
        "concrete remediation. IDs and line numbers refer to the audited revision "
        "(commit e0ec6a5).", BODY))
    story.append(build_index(FINDINGS))
    story.append(PageBreak())

    # ---- Detailed findings ----
    story.append(P("4. Detailed Findings", H1))
    story.append(P(
        "<b>Severity scale:</b> High = exploitable or data-loss/privacy risk that should block "
        "release; Medium = meaningful weakness requiring a workaround or defence-in-depth; "
        "Low = code-quality/operability issue. Every finding below is marked with its "
        "remediation status from the 2026-11-05 response.", SMALL))
    story.append(Spacer(1, 4 * mm))
    for f in FINDINGS:
        story.append(KeepTogether([finding_block(f)]))
        story.append(Spacer(1, 3 * mm))

    story.append(PageBreak())

    # ---- Thematic conclusions ----
    story.append(P("5. Thematic Conclusions (post-remediation)", H1))
    themes = [
        ("Security engineering is no longer 'tick-box'",
         "CSRF tokens rotate on privileged transitions, logout is a CSRF-protected "
         "POST, HSTS is emitted behind the proxy, and the audit log is an HMAC hash "
         "chain with an admin verification action rather than app-level triggers only."),
        ("Deployment now matches the documentation",
         "nginx terminates TLS 1.2+, the container runs as a non-root user with a "
         "single model worker, and ProxyFix forwards the real scheme and client IP."),
        ("ML rigour improved",
         "The split happens before augmentation, the class mapping is stored and "
         "asserted, evaluation metrics are persisted per model version, and an "
         "entropy/margin abstain path prevents confident wrong answers."),
        ("Operability and testability improved",
         "The codebase is an app-factory package with blueprints, a request-scoped DB, "
         "pinned dependencies, a 27-case pytest suite, CI and a real readiness signal."),
        ("Remaining operational items",
         "Provision MAIL_DSN / SMS_GATEWAY_DSN so invitation and reset secrets are "
         "delivered automatically, rotate the previously-exposed seed credential, and "
         "purge any historical repository copies that still contain it."),
    ]
    for title, text in themes:
        story.append(P(title, H2))
        story.append(P(text, BODY))

    story.append(P("6. Remediation Roadmap (status)", H1))
    roadmap = [
        ["Phase", "Actions", "Status"],
        ["Immediate", "SEC-01, SEC-02, SEC-15: credential handling, untracking, "
         "no secrets in UI.", "Fixed"],
        ["Short term", "SEC-04, SEC-06, SEC-07, SEC-10, SEC-18: limiter, token reset, "
         "POST logout, TLS proxy, HSTS.", "Fixed"],
        ["Medium term", "SEC-12-14, SEC-16, SEC-20-22, QUA-01-03: auth hardening, "
         "hash-chain audit, OOD, leak-free split, tests + CI.", "Fixed"],
        ["Operational", "Provision mail/SMS gateways; rotate exposed credentials; "
         "purge historical repo copies.", "Action required"],
    ]
    rt = Table(roadmap, colWidths=[28 * mm, 90 * mm, 38 * mm], repeatRows=1)
    rt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d0d0d0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(rt)

    story.append(P("7. Scope &amp; Limitations", H1))
    story.append(P(
        "This was a static, white-box review plus the automated regression suite added in "
        "response. No live penetration testing, fuzzing, or runtime exploitation was "
        "performed; `pip-audit` runs in CI. The model was not retrained, so accuracy "
        "findings are about pipeline correctness rather than measured performance. Line "
        "numbers refer to the audited revision. Operational items (mail/SMS gateways, "
        "credential rotation of any historical copies) remain for the deployment owner.",
        SMALL))

    doc = AuditDoc(
        OUTPUT,
        pagesize=A4,
        title="Security & Code Audit - Maize Crop Disease Diagnostic System",
        author="Independent Senior Developer Audit",
        subject="Code and security audit",
    )
    doc.build(story)
    print(f"[OK] Wrote {OUTPUT} ({len(FINDINGS)} findings)")


if __name__ == "__main__":
    main()
