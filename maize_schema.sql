-- =====================================================================
-- Maize Disease Diagnostic System: relational schema (Third Normal Form)
-- Target engine: SQLite 3 (WAL mode); portable to MySQL/PostgreSQL.
-- Run once per connection:  PRAGMA foreign_keys = ON;  PRAGMA journal_mode = WAL;
-- =====================================================================

-- ---------- Reference (lookup) tables ----------
CREATE TABLE role (
    role_id      INTEGER PRIMARY KEY,
    role_name    TEXT NOT NULL UNIQUE
                 CHECK (role_name IN ('FARMER','EXTENSION_OFFICER','ADMIN')),
    description  TEXT NOT NULL
);

CREATE TABLE village_section (          -- removes repeating location text (1NF/3NF)
    section_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    section_name TEXT NOT NULL UNIQUE,
    epa_name     TEXT NOT NULL DEFAULT 'Liwaladzi'
);

CREATE TABLE disease_class (            -- one row per model output class
    class_id        INTEGER PRIMARY KEY,   -- equals the model output index 0..3
    class_name      TEXT NOT NULL UNIQUE,  -- Downy_Mildew, Healthy, MLN, MSV
    display_name    TEXT NOT NULL,
    scientific_info TEXT,
    is_disease      INTEGER NOT NULL CHECK (is_disease IN (0,1))
);

CREATE TABLE advisory (                 -- rule-based IPM advice, one row per class and language
    advisory_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id      INTEGER NOT NULL REFERENCES disease_class(class_id),
    language_code TEXT NOT NULL CHECK (language_code IN ('en','ny')),
    status_text   TEXT NOT NULL,
    description   TEXT NOT NULL,
    action_text   TEXT NOT NULL,
    source_ref    TEXT NOT NULL,          -- authority for the advice (IPM guideline)
    UNIQUE (class_id, language_code)
);

CREATE TABLE event_type (               -- controlled vocabulary for audit events
    event_type_id INTEGER PRIMARY KEY,
    event_code    TEXT NOT NULL UNIQUE,   -- e.g. LOGIN_SUCCESS, MODEL_INFERENCE
    category      TEXT NOT NULL CHECK (category IN
                  ('AUTHENTICATION','DIAGNOSIS','ADMINISTRATION','ACCESS_CONTROL','SYSTEM')),
    description   TEXT NOT NULL
);

-- ---------- Core entities ----------
CREATE TABLE app_user (
    user_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name      TEXT NOT NULL,
    username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email          TEXT UNIQUE COLLATE NOCASE,
    phone_number   TEXT UNIQUE,
    password_hash  TEXT NOT NULL,          -- salted hash only, never plain text
    role_id        INTEGER NOT NULL REFERENCES role(role_id),
    account_status TEXT NOT NULL DEFAULT 'ACTIVE'
                   CHECK (account_status IN ('ACTIVE','DEACTIVATED','LOCKED')),
    section_id     INTEGER REFERENCES village_section(section_id),
    must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0,1)),
    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_until   TEXT,
    last_login_at  TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by     INTEGER REFERENCES app_user(user_id),   -- NULL for self-registered farmers
    CHECK (email IS NOT NULL OR phone_number IS NOT NULL)
);

-- Officer vetting: farmers self-register; officers can ONLY be created from an invitation
-- issued by an administrator. The invitation is single-use, expires, and stores only a hash.
CREATE TABLE officer_invitation (
    invitation_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    invitee_name   TEXT NOT NULL,
    invitee_contact TEXT NOT NULL,         -- e-mail or phone the admin verified out of band
    employee_ref   TEXT NOT NULL,          -- ministry/district staff reference checked by admin
    role_id        INTEGER NOT NULL DEFAULT 2 REFERENCES role(role_id),  -- invited role: 2=officer, 3=admin
    token_hash     TEXT NOT NULL UNIQUE,   -- hash of the one-time code; code itself never stored
    issued_by      INTEGER NOT NULL REFERENCES app_user(user_id),
    issued_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    expires_at     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'PENDING'
                   CHECK (status IN ('PENDING','USED','EXPIRED','REVOKED')),
    used_by        INTEGER UNIQUE REFERENCES app_user(user_id),
    used_at        TEXT,
    CHECK (expires_at > issued_at),
    CHECK ((status = 'USED') = (used_by IS NOT NULL))
);

-- Officers are scoped to the village sections they may report on (SEC-11).
CREATE TABLE officer_section (
    user_id    INTEGER NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    section_id INTEGER NOT NULL REFERENCES village_section(section_id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, section_id)
);

-- Pre-registration pending records: the account is created only after the
-- contact (email or phone) proves ownership of a Brevo-delivered code (FR-02).
CREATE TABLE registration_verification (
    verification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id      TEXT NOT NULL UNIQUE,       -- random id used in the verify URL
    contact        TEXT NOT NULL,              -- normalised email or phone
    contact_kind   TEXT NOT NULL CHECK (contact_kind IN ('email','phone')),
    full_name      TEXT NOT NULL,
    username       TEXT NOT NULL,
    section_id     INTEGER NOT NULL REFERENCES village_section(section_id),
    password_hash  TEXT NOT NULL,
    code_hash      TEXT NOT NULL,              -- SHA-256 of the 6-digit code
    attempts       INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    expires_at     TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_regver_contact ON registration_verification(contact);

-- Single-use, hashed, expiring password-reset tokens (SEC-06).
CREATE TABLE password_reset_token (
    token_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    expires_at TEXT NOT NULL,
    used_at    TEXT
);

CREATE TABLE diagnosis (
    diagnosis_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES app_user(user_id),
    class_id       INTEGER NOT NULL REFERENCES disease_class(class_id),
    section_id     INTEGER REFERENCES village_section(section_id),  -- location of the field, chosen by user
    image_sha256   TEXT NOT NULL,          -- fingerprint; image stored on disk, not in the DB
    image_path     TEXT,                   -- NULL if the image was not retained
    model_version  TEXT NOT NULL,
    inference_ms   INTEGER NOT NULL CHECK (inference_ms >= 0),
    total_ms       INTEGER CHECK (total_ms >= 0),
    saved_to_history INTEGER NOT NULL DEFAULT 1 CHECK (saved_to_history IN (0,1)),
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE diagnosis_probability (    -- full softmax output; the confidence of the predicted class is read from here (no duplicated column)
    diagnosis_id   INTEGER NOT NULL REFERENCES diagnosis(diagnosis_id) ON DELETE CASCADE,
    class_id       INTEGER NOT NULL REFERENCES disease_class(class_id),
    probability    REAL NOT NULL CHECK (probability BETWEEN 0 AND 1),
    PRIMARY KEY (diagnosis_id, class_id)
);

-- Convenience view: predicted class plus its confidence, derived rather than stored twice.
CREATE VIEW v_diagnosis_detail AS
SELECT d.diagnosis_id, d.user_id, d.class_id, c.class_name, p.probability AS confidence,
       d.section_id, d.model_version, d.inference_ms, d.total_ms, d.created_at
FROM diagnosis d
JOIN disease_class c ON c.class_id = d.class_id
JOIN diagnosis_probability p ON p.diagnosis_id = d.diagnosis_id AND p.class_id = d.class_id;

-- 6-digit codes for password reset (email/SMS verification before the URL
-- token is issued). Single-use, hashed, 10-minute expiry, attempt-limited.
CREATE TABLE password_reset_code (
    code_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id  TEXT NOT NULL UNIQUE,           -- random id kept in the session
    user_id    INTEGER NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,                  -- SHA-256 of the 6-digit code
    attempts   INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_resetcode_user ON password_reset_code(user_id);

-- ---------- Audit trail (append-only) ----------
CREATE TABLE system_log (
    log_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    user_id        INTEGER REFERENCES app_user(user_id),   -- NULL when actor unknown (failed login)
    event_type_id  INTEGER NOT NULL REFERENCES event_type(event_type_id),
    outcome        TEXT NOT NULL CHECK (outcome IN ('SUCCESS','FAILURE','DENIED')),
    source_ip      TEXT,                   -- request origin; no GPS or device fingerprint
    request_path   TEXT,
    target_user_id INTEGER REFERENCES app_user(user_id),   -- admin actions on another account
    diagnosis_id   INTEGER REFERENCES diagnosis(diagnosis_id),
    detail         TEXT,                   -- short context; never passwords, tokens or images
    prev_hash      TEXT,                   -- SEC-16: hash-chain linkage
    row_hash       TEXT                    -- HMAC(secret, canonical row + prev_hash)
);

-- Audit records can never be changed or removed by the application.
CREATE TRIGGER system_log_no_update BEFORE UPDATE ON system_log
BEGIN SELECT RAISE(ABORT, 'system_log is append-only'); END;
CREATE TRIGGER system_log_no_delete BEFORE DELETE ON system_log
BEGIN SELECT RAISE(ABORT, 'system_log is append-only'); END;

-- Officers can only come from invitations: block direct creation of privileged users by role check.
CREATE TRIGGER officer_requires_invitation BEFORE INSERT ON app_user
WHEN NEW.role_id IN (SELECT role_id FROM role WHERE role_name = 'EXTENSION_OFFICER')
 AND NEW.created_by IS NULL
BEGIN SELECT RAISE(ABORT, 'extension officers must be created via invitation or by an administrator'); END;

CREATE TRIGGER no_self_registered_admin BEFORE INSERT ON app_user
WHEN NEW.role_id IN (SELECT role_id FROM role WHERE role_name = 'ADMIN')
 AND NEW.created_by IS NULL
 AND (SELECT COUNT(*) FROM app_user) > 0
BEGIN SELECT RAISE(ABORT, 'administrators cannot self-register'); END;

-- ---------- Indexes for dashboard and audit queries ----------
CREATE INDEX idx_diag_user_time    ON diagnosis(user_id, created_at DESC);
CREATE INDEX idx_diag_class_time   ON diagnosis(class_id, created_at);
CREATE INDEX idx_diag_section_time ON diagnosis(section_id, created_at);
CREATE INDEX idx_log_time          ON system_log(occurred_at DESC);
CREATE INDEX idx_log_user_time     ON system_log(user_id, occurred_at DESC);
CREATE INDEX idx_log_event_time    ON system_log(event_type_id, occurred_at DESC);
CREATE INDEX idx_user_role_status  ON app_user(role_id, account_status);
CREATE INDEX idx_reset_token_hash  ON password_reset_token(token_hash);
CREATE INDEX idx_officer_section   ON officer_section(user_id);

-- ---------- Seed data ----------
INSERT INTO role VALUES (1,'FARMER','Local smallholder farmer'),
                        (2,'EXTENSION_OFFICER','Extension worker (invitation only)'),
                        (3,'ADMIN','System administrator');
                        
INSERT INTO disease_class VALUES (0,'Downy_Mildew','Downy Mildew','Peronosclerospora sorghi',1),
                                 (1,'Healthy','Healthy',NULL,0),
                                 (2,'MLN','Maize Lethal Necrosis (MLN)','MCMV + potyvirus co-infection',1),
                                 (3,'MSV','Maize Streak Virus (MSV)','Maize streak virus',1);

INSERT INTO event_type VALUES
 (1,'REGISTER_FARMER','AUTHENTICATION','Farmer self-registration'),
 (2,'REGISTER_OFFICER','AUTHENTICATION','Officer account created from invitation'),
 (3,'LOGIN_SUCCESS','AUTHENTICATION','Successful sign-in'),
 (4,'LOGIN_FAILURE','AUTHENTICATION','Failed sign-in'),
 (5,'LOGOUT','AUTHENTICATION','Sign-out or session expiry'),
 (6,'ACCOUNT_LOCKED','AUTHENTICATION','Lock-out after repeated failures'),
 (7,'MODEL_INFERENCE','DIAGNOSIS','Leaf image classified'),
 (8,'DASHBOARD_VIEW','ACCESS_CONTROL','Officer dashboard opened'),
 (9,'REPORT_EXPORT','ACCESS_CONTROL','CSV report exported'),
 (10,'INVITATION_ISSUED','ADMINISTRATION','Admin issued officer invitation'),
 (11,'INVITATION_REVOKED','ADMINISTRATION','Admin revoked invitation'),
 (12,'USER_STATUS_CHANGE','ADMINISTRATION','Account activated/deactivated'),
 (13,'ROLE_CHANGE','ADMINISTRATION','Role reassigned'),
 (14,'LOG_VIEW','ADMINISTRATION','Audit log viewed'),
 (15,'UNAUTHORIZED_ATTEMPT','ACCESS_CONTROL','Access refused for role'),
 (16,'SYSTEM_ERROR','SYSTEM','Unhandled application error'),
 (17,'PASSWORD_CHANGE','AUTHENTICATION','User changed own password'),
 (18,'PASSWORD_RESET','ADMINISTRATION','Administrator reset a password'),
 (19,'PASSWORD_RESET_REQUESTED','AUTHENTICATION','User requested a password reset token'),
 (20,'MODEL_OOD','DIAGNOSIS','Image rejected as out-of-distribution / low confidence'),
 (21,'AUDIT_VERIFY','SYSTEM','Audit hash-chain verification run');