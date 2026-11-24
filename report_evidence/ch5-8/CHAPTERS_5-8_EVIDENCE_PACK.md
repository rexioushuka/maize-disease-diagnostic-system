# Chapters 5–8 Evidence Pack

Use this file to finish Chapters 5–8 of `project report guide-1.docx`. Every number below comes from a stored artifact in this repository. Do not claim H3/usability, Android/field deployment, 35-concurrent-session capacity, HTTPS uptime, or 12-month log retention: no evidence for those exists yet.

## 0. Source of truth

All paths are relative to `C:\Users\Rexious Huka\maize_disease_system`.

| Evidence | Path | Verified result |
|---|---|---|
| Full pytest log | `report_evidence/ch5-8/pytest_run.log` | `69 passed in 208.99s (0:03:28)` |
| Full pytest XML | `report_evidence/ch5-8/pytest_results.xml` | 69 passed, 0 failed, 0 errors |
| New report-case/DB tests | `tests/test_db_safeguards.py`, `tests/test_report_cases.py`, `report_evidence/ch5-8/pytest_report_db_cases.log` | `31 passed in 97.27s (0:01:37)` |
| DB safeguard evidence | `report_evidence/ch5-8/db_safeguard_checks.json` | `12/12 DB checks passed` |
| Timing evidence | `report_evidence/ch5-8/latency_30_runs.json`, `report_evidence/ch5-8/latency_30_runs.csv`, `report_evidence/ch5-8/latency_server.log` | 30/30 successes; median `469.2 ms`; p95 `2139.5 ms` |
| Screenshot inventory | `report_evidence/ch5-8/screenshots.json`, `report_evidence/ch5-8/screenshots/`, `report_evidence/ch5-8/screenshots/html/`, `report_evidence/ch5-8/screenshot_server.log` | 9 live-app screenshots + HTML snapshots |
| Model evaluation | `models/evaluation_report_v2.json`, `models/evaluate2_run.txt` | accuracy `0.9711692471970101`, N = `1873` |
| Split counts | `report_evidence/ch5-8/dataset_split_counts.csv` | total `12,467` images |
| Per-class metrics | `report_evidence/ch5-8/per_class_metrics.csv` | all classes above 0.85 |
| Confusion matrix | `report_evidence/ch5-8/confusion_matrix_v2.png` | current evaluation plot |
| Environment | `report_evidence/ch5-8/system_info.json` | Python `3.13.14`, Windows `11 10.0.26200-SP0`, SQLite `3.50.4` |

Reproduce with:

```powershell
python -m pytest tests -v --junitxml=report_evidence/ch5-8/pytest_results.xml
python report_evidence/ch5-8/generate_db_evidence.py
python report_evidence/ch5-8/measure_latency.py
python report_evidence/ch5-8/capture_screenshots.py
```

Full-suite breakdown: `tests/test_auth.py` 12, `tests/test_db_safeguards.py` 12, `tests/test_diagnosis.py` 8, `tests/test_password_reset_code.py` 6, `tests/test_register_verification.py` 5, `tests/test_report_cases.py` 19, `tests/test_security.py` 7.

## 1. Must-fix stale claims in the guide

| Stale draft claim | Use this instead |
|---|---|
| “It has no accounts, database or log” | The finished system has farmer/officer/admin accounts, a SQLite database, an append-only audit log, dashboards, invitation activation, password reset, CSRF, role checks and rate limits. |
| “2 Gunicorn workers” / “2 copies of the model” | `Dockerfile` uses `gunicorn --workers 1 --threads 4 --timeout 120 wsgi:app`; one worker keeps one model copy. |
| “Python 3.10 / unpinned versions / Flask 3.0 / TensorFlow 2.13 / SQLite 3.45.1” | Runtime is pinned in `requirements.txt`: Flask `3.1.3`, Werkzeug `3.1.9`, tensorflow-cpu `2.21.0`, Pillow `12.3.0`, numpy `2.5.3`, gunicorn `26.2.0`. Verification used Python `3.13.14`, pytest `9.2.0`, requests `2.34.2`, SQLite `3.50.4`. |
| `mobilenetv2_maize.h5` | `models/mobilenetv2_maize.keras`; SHA-256 `8b362f6428019bccc314b263f1890a5f5ae7fa0f0cf79ce4e201a73845be17d1`. |
| “schema creates 10 tables” | `maize_schema.sql` creates 14 tables: role, village_section, disease_class, advisory, event_type, app_user, officer_invitation, officer_section, registration_verification, password_reset_token, diagnosis, diagnosis_probability, password_reset_code, system_log. |
| “Screenshots are required / mock-ups only” | 9 real screenshots were captured from the running app; see Section 4. |
| “Only DB tests executed / TC-01 to TC-16 not executed” | Full suite: 69 passed. TC-01–TC-10 and TC-12–TC-16 are pytest-visible; TC-11 is the 30-run HTTP timing test. |
| “Hashing, lock-out, sessions, CSRF, rate limiting not yet built” | All are implemented and tested; see `tests/test_auth.py`, `tests/test_report_cases.py`, `tests/test_security.py`, `msds/config.py`, `msds/security_core.py`. |
| D-03/D-04/D-06 “Open” | Fixed: generic error text in `msds/__init__.py`; no `flask_cors`/`CORS(` in runtime code; `MAX_UPLOAD_BYTES`/`MAX_CONTENT_LENGTH` and extension allowlist in `msds/config.py`. |
| “H2 not yet tested; about 3.1 s” | 30 real HTTP diagnoses: median `469.2 ms`, p95 `2139.5 ms`; both targets met. Disclose the `11557.7 ms` first-request cold-start outlier. |
| “O3/O4 partly met; application not built” | O3 is met for the implemented system. O4 is partly met: model and H2 measured, H3 not measured. |
| “Finish application and test it” in Chapter 8 future work | Replace with field validation, H3 measurement, local test images, D-01/D-02 remediation, and deployment validation. |

## 2. Chapter 5 ready-to-use evidence

### 5.2 Architecture status

Replace the stale “prototype only” paragraph with:

> The implemented system follows the three-tier design in Figure 5.1. A browser-facing Flask application renders responsive HTML/CSS/Bootstrap templates for the farmer, extension-officer and administrator roles. The application factory in `msds/__init__.py` registers the public, authentication, farmer, officer and admin blueprints, loads the model once per process, and installs generic error handlers for 400, 401, 403, 404, 413, 429 and 500 responses. The data tier is SQLite with foreign keys enabled, WAL mode, an image store outside the web root, and a SHA-256 fingerprint per stored image. The model is `models/mobilenetv2_maize.keras` (MobileNetV2-v1.1). Production packaging uses a non-root Docker user, one Gunicorn worker with four threads, pinned dependencies, persistent volumes and a `/health` check.

Security controls are implemented at the design layers: server-side role checks (`msds/security_core.py:80`), CSRF tokens (`msds/security_core.py:42`), 30-minute idle sessions and 15-minute lockout after five failed logins (`msds/config.py:76-85`), password hashing via Werkzeug, per-route rate limiting (`msds/rate_limit.py`), generic errors and audit logging. No cross-origin wildcard is present in the runtime code.

### 5.7 Test design executed

| ID | Evidence | Status |
|---|---|---|
| TC-01 | `test_TC01_register_farmer_and_hash_password` | Pass |
| TC-02 | `test_TC02_signup_role_value_is_ignored[ADMIN]`, `[EXTENSION_OFFICER]` | Pass (2 cases) |
| TC-03 | `test_TC03_officer_direct_insert_without_creator_blocked` | Pass |
| TC-04 | `test_TC04_officer_activation_valid_reuse_expired_wrong_contact` | Pass |
| TC-05 | `test_TC05_lockout_after_five_failures_and_recovery` | Pass |
| TC-06 | `test_TC06_idle_session_expires_after_thirty_minutes` | Pass |
| TC-07 | `test_TC07_farmer_is_denied_officer_and_admin_pages` | Pass |
| TC-08 | `test_TC08_invalid_uploads_are_rejected_without_crashing` | Pass |
| TC-09 | `test_TC09_each_class_image_returns_class_confidence_and_advisory` (4 classes) | Pass (4 cases) |
| TC-10 | `test_TC10_low_confidence_and_ood_are_flagged` | Pass |
| TC-11 | `report_evidence/ch5-8/latency_30_runs.json` | Pass; median/p95 targets met |
| TC-12 | `test_TC12_history_is_visible_only_to_its_owner` | Pass |
| TC-13 | `test_TC13_officer_totals_match_database_and_csv_is_anonymous` | Pass |
| TC-14 | `test_TC14_log_table_is_append_only_and_chain_verifies` | Pass |
| TC-15 | `test_TC15_last_active_administrator_cannot_be_deactivated` | Pass |
| TC-16 | `test_TC16_backup_restore_matches_row_counts` | Pass |

Important qualifications:

- TC-09 checks that each class image returns a valid class, confidence, non-empty advisory/source and four probabilities. It does not assert exact ground-truth equality; exact held-out-set accuracy is reported in Chapter 7.
- TC-15 is implemented as a refusal to deactivate the signed-in administrator (`"cannot deactivate your own account"`). This blocks the normal route to the last-admin state and is stronger than the abstract trigger rule.
- TC-16 uses the SQLite online backup API and verifies row counts and status restoration in under 30 minutes. A daily scheduler is not proven by this test.

### Database safeguards

`report_evidence/ch5-8/db_safeguard_checks.json`: 12 total, 12 passed, 0 failed, with `PRAGMA foreign_keys = ON`.

| ID | Check | Expected | Actual | Status |
|---|---|---|---|---|
| DB-01 | Bootstrap first administrator | Allowed | Allowed | Pass |
| DB-02 | Self-registered farmer | Allowed | Allowed | Pass |
| DB-03 | Officer without creator | Blocked | Blocked | Pass |
| DB-04 | Self-registered administrator after the first | Blocked | Blocked | Pass |
| DB-05 | Officer created by an administrator | Allowed | Allowed | Pass |
| DB-06 | Insert a log entry | Allowed | Allowed | Pass |
| DB-07 | Update a log entry | Blocked | Blocked | Pass |
| DB-08 | Delete a log entry | Blocked | Blocked | Pass |
| DB-09 | User with neither e-mail nor phone | Blocked | Blocked | Pass |
| DB-10 | Probability above 1 | Blocked | Blocked | Pass |
| DB-11 | Duplicate username in different letter case | Blocked | Blocked | Pass |
| DB-12 | Diagnosis for a user that does not exist | Blocked | Blocked | Pass |

## 3. Chapter 6 ready-to-use evidence

### 6.x Implementation status table

| Part | Status | Evidence |
|---|---|---|
| Dataset preparation script | Built | `prep_dataset.py`; `report_evidence/ch5-8/dataset_split_counts.csv` |
| MobileNetV2 training/evaluation | Built and run | `models/evaluation_report_v2.json`, `models/evaluate2_run.txt` |
| Application factory and routes | Built and tested | `msds/__init__.py`, `tests/test_auth.py`, `tests/test_report_cases.py` |
| Farmer registration, login, lockout, sessions | Built and tested | `tests/test_auth.py`, `tests/test_report_cases.py` |
| Officer invitation/activation | Built and tested | `tests/test_register_verification.py`, `tests/test_report_cases.py` |
| Diagnosis, history, dashboard, CSV export | Built and tested | `tests/test_diagnosis.py`, `tests/test_report_cases.py` |
| Administrator console and audit chain | Built and tested | `tests/test_diagnosis.py`, `tests/test_report_cases.py` |
| Security controls | Built and tested | `tests/test_security.py`, `msds/config.py`, `msds/security_core.py` |
| Database schema and safeguards | Built and tested | `maize_schema.sql`, `db_safeguard_checks.json` |
| Backup utility/restore test | Built and tested | `backup_db.py`, `test_TC16_backup_restore_matches_row_counts` |
| Docker production image | Built; not deployed/validated in this evidence set | `Dockerfile` |
| HTTPS/LAN field deployment | Not verified | No evidence artifact |

### Environment table

Replace the stale environment table with:

| Tool | Exact version/use |
|---|---|
| Python | `3.13.14` used for verification; `requirements.txt` runtime is pinned |
| Flask / Werkzeug | `3.1.3` / `3.1.9` |
| tensorflow-cpu | `2.21.0` |
| Pillow / NumPy | `12.3.0` / `2.5.3` |
| Gunicorn | `26.2.0`, `1` worker, `4` threads, `120 s` timeout |
| SQLite | `3.50.4` verification; WAL + `PRAGMA foreign_keys=ON` |
| pytest / requests | `9.2.0` / `2.34.2` |
| Docker base | `python:3.10-slim` (`Dockerfile`) |
| Chrome for screenshots | `Google Chrome 158.0.8127.37` |
| Verification host | Intel i5-10210U, 4 cores/8 threads, 4 GB RAM, Windows 11 `10.0.26200-SP0` |

### Main modules

Use:

> The final model path is `models/mobilenetv2_maize.keras`. `msds/services/model_loader.py` verifies the model SHA-256 against `models/class_mapping.json`, loads with `safe_mode=True, compile=False`, and checks that the output dimension matches the four authoritative class names. The model is loaded once per process at application startup; production runs one Gunicorn worker with four threads. Uploads are limited to 5 MB at the application boundary, restricted to `.png`, `.jpg` and `.jpeg`, decoded and re-encoded before inference; the browser additionally resizes to at most 500 KB and 1024 px longest side (`static/js/app.js:10-11`). Rule-based advisories are stored per class and language with source references; values below 0.70 confidence and entropy/margin OOD signals are flagged rather than presented as certain diagnoses.

`maize_schema.sql` contains 14 tables, one view (`v_diagnosis_detail`) and triggers for append-only logs, officer creation and administrator self-registration. It does not duplicate confidence in the diagnosis table.

### Screenshots

All screenshots were captured from authenticated pages of the running Flask application with the real model in headless Chrome. PNGs are non-blank (verified by pixel variance); HTML snapshots are stored beside them.

| No. | File | Viewport | What it evidences |
|---|---|---|---|
| S1 | `report_evidence/ch5-8/screenshots/S1_farmer_home.png` | 360×900 | Farmer upload/home page on phone width |
| S2 | `report_evidence/ch5-8/screenshots/S2_diagnosis_result.png` | 360×1100 | Diagnosis result with class, confidence, advisory/probabilities |
| S3 | `report_evidence/ch5-8/screenshots/S3_sign_in.png`, `S3_sign_up.png` | 360×900 | Sign-in and farmer sign-up; account type fixed to Farmer |
| S4 | `report_evidence/ch5-8/screenshots/S4_officer_activation.png` | 360×900 | Officer invitation-code activation form |
| S5 | `report_evidence/ch5-8/screenshots/S5_farmer_history.png` | 360×1100 | Farmer's own diagnosis history |
| S6 | `report_evidence/ch5-8/screenshots/S6_officer_dashboard.png` | 1366×1000 | Officer totals, class breakdown, filters, export |
| S7 | `report_evidence/ch5-8/screenshots/S7_admin_users.png` | 1400×1000 | Admin user list, invitation and deactivation controls |
| S8 | `report_evidence/ch5-8/screenshots/S8_admin_system_log.png` | 1400×1200 | Admin system log with filters and newest-first entries |

### Test results

Use:

> The final test run executed the whole suite with `python -m pytest tests -v --junitxml=report_evidence/ch5-8/pytest_results.xml`. All 69 tests passed in 208.99 seconds. This includes 12 authentication tests, 12 database safeguard checks, 8 diagnosis tests, 6 password-reset-code tests, 5 registration-verification tests, 19 report-case tests covering TC-01 to TC-10 and TC-12 to TC-16, and 7 security tests. TC-11 was executed separately as the 30-request timing test recorded in `latency_30_runs.json`. Passing these tests shows that the specified cases passed; it does not establish freedom from all defects.

### Defect status

| ID | Status to report | Basis |
|---|---|---|
| D-01 | Open | Augmentation before split remains; `dataset_split_counts.csv` and guide's history. |
| D-02 | Open | Final preparation script/seed mismatch remains unresolved. |
| D-03 | Fixed | Generic errors in `msds/__init__.py:124-134`; negative/tamper paths tested. |
| D-04 | Fixed | No `flask_cors` or `CORS(` in runtime code; not in `requirements.txt`. |
| D-05 | Fixed | `v_diagnosis_detail` derives confidence; no duplicate stored column. |
| D-06 | Fixed | `MAX_UPLOAD_BYTES`/`MAX_CONTENT_LENGTH` and extension allowlist; TC-08 passes. |
| D-07 | Mitigated by design | Low-confidence flag and OOD signal; four-class limitation stated in UI. |
| D-08 | To verify | No training log proving which epoch produced the saved model. Do not claim verified. |

## 4. Chapter 7 ready-to-use evidence

### Model evaluation

Use the existing Chapter 7 text for definitions and error analysis, but update the evidence references to `models/evaluation_report_v2.json` and `report_evidence/ch5-8/confusion_matrix_v2.png`. Keep the leakage and non-local-data caveats.

| Class | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| Downy Mildew | 375 | 0.9973 | 1.0000 | 0.9987 |
| Healthy | 748 | 0.9722 | 0.9826 | 0.9774 |
| MLN | 375 | 0.9465 | 0.9440 | 0.9453 |
| MSV | 375 | 0.9673 | 0.9467 | 0.9569 |
| Macro average | 1,873 | 0.9708 | 0.9683 | 0.9695 |
| Weighted average | 1,873 | 0.9711 | 0.9712 | 0.9711 |

Accuracy: `0.9711692471970101` (97.12%). Confusion matrix rows in `evaluation_metrics_summary.json`: `[[375,0,0,0],[0,735,10,3],[1,11,354,9],[0,10,10,355]]`.

### H2 timing test

Use:

> H2 was tested with 30 consecutive real HTTP `POST /api/v1/predict` requests against `python app.py` using the real MobileNetV2 model and test images up to 301,787 bytes. All 30 requests succeeded. Median wall-clock upload-to-result time was 469.2 ms and the 95th percentile was 2139.5 ms, both below the 5 s and 10 s targets. The mean was 1007.3 ms; the maximum was 11557.7 ms on the first request, which includes cold-start cost and is reported rather than hidden. Raw runs are in `latency_30_runs.json`/`.csv`.

| Measure | Result | Target | Met? |
|---|---:|---:|---|
| Runs / successes | 30 / 30 | 30 | Yes |
| Median wall time | 469.2 ms | ≤ 5,000 ms | Yes |
| p95 wall time | 2139.5 ms | ≤ 10,000 ms | Yes |
| Mean wall time | 1007.3 ms | Reported only | — |
| Max wall time | 11557.7 ms | Reported only | — |
| Median server total | 371.0 ms | Reported only | — |

### Objectives and requirements

| Objective | Status | Evidence |
|---|---|---|
| O1 analyse practices/needs | Met | Existing Chapters 3–4 interview evidence. |
| O2 design architecture/database/security/interfaces | Met | Chapter 5; 14-table schema; 12/12 DB checks; security controls tested. |
| O3 implement classifier, access control, diagnosis, dashboard, audit log | Met for implemented scope | 69 passing tests; 9 live screenshots; full module implementation. |
| O4 evaluate model, speed and user acceptance | Partly met | Model metrics and H2 measured; H3 and field acceptance not measured. |

| Requirement group | Status | Evidence |
|---|---|---|
| FR-01 to FR-06 | Implemented/tested | auth tests, TC-01/02/05/06/07 |
| FR-07 to FR-10 | Implemented/tested | TC-08/09/10, diagnosis tests |
| FR-11 to FR-13 | Implemented/tested | low-confidence/OOD flags, TC-12, schema and diagnosis tests |
| FR-14, FR-15 | Implemented/tested | TC-13 |
| FR-16 to FR-18 | Implemented/tested | activation/registration tests, TC-04/15 |
| FR-19 to FR-21 | Implemented/tested | TC-07/14, audit chain tests |
| FR-22, FR-23 | Implemented | `msds/i18n.py` English/Chichewa strings and photo guidance; not independently enumerated as 100% in this evidence set |
| NFR-01 | Met | 30-run timing above |
| NFR-02 | Met on held-out set | all per-class scores ≥ 0.9440, with D-01/non-local caveats |
| NFR-03 | Implemented | client resize `500 KB`/`1024 px`; server 5 MB cap; client resize not directly unit-tested |
| NFR-04 | Not verified | 35 concurrent sessions not load tested |
| NFR-05 | Not verified | 95% uptime not measured |
| NFR-06 | Tested | password hashes, lockout, roles, CSRF, sessions, rate limits, generic errors; `tests/test_security.py` |
| NFR-07 | Implemented; completeness not audited | `msds/i18n.py` `en`/`ny` |
| NFR-08 | Partially verified | desktop/360 px Chrome screenshots; Android/field device not tested |
| NFR-09 | Not measured | H3 needs participant data |
| NFR-10 | Restorability tested; schedule not verified | TC-16; no daily scheduler evidence |
| NFR-11 | Partially verified | append-only/chain tests; 100% event coverage and 12-month retention not proven |

### H3/usability

Keep the planned SUS and Likert instrument, but do not fill its result table. Use:

> H3 remains untested. No post-use participant responses, SUS scores, or Android field-session records exist in the evidence set. Table 7.4 must remain a blank instrument or be completed only after real participants use the system. The speed part of user experience is represented by the developer-host H2 measurement, not by participant-rated speed.

### Hypotheses

Replace the stale hypothesis paragraph with:

> H1 is supported on the held-out test set: accuracy is 97.12% and every per-class precision, recall and F1-score exceeds 0.85, subject to D-01 leakage and the non-local training data. H2 is supported by 30 real HTTP diagnoses with median 469.2 ms and p95 2139.5 ms. H3 is not tested because no post-use user ratings were collected. No statistical significance claim is made.

## 5. Chapter 8 ready-to-use evidence

### Summary/key findings

Use:

> The finished system implements the designed farmer, extension-officer and administrator workflows, including invitation-only officer activation, diagnosis with rule-based bilingual advisories, personal history, officer dashboards and anonymised CSV export, administrator account controls, an append-only audit log with hash-chain verification, and the specified security controls. The full suite of 69 tests passed, including 12 database safeguard checks and the application test cases TC-01–TC-10 and TC-12–TC-16. Nine live screenshots and a 30-request timing run document the implemented system.

### Limitations

Use:

> The classifier still has two important data caveats: Downy Mildew was augmented before splitting (D-01), and the preparation script/seed used for the final model was not preserved (D-02). Training images are not local, so field performance in Liwaladzi is unproven. H3 user acceptance, Android compatibility, 35-concurrent-session capacity, HTTPS uptime and 12-month log retention were not measured. Passing tests covers the specified cases only.

### Future work

Replace the stale “finish the application” bullet with:

1. Split original images before augmentation, preserve the final preparation script and random seed, retrain, and re-evaluate (D-01, D-02).
2. Collect labelled Liwaladzi field images and report held-out local performance separately.
3. Run the planned post-use SUS/Likert evaluation with real participants to test H3.
4. Validate HTTPS/LAN deployment, Android Chrome access, and 35 concurrent sessions.
5. Verify the saved training epoch against a retained training log (D-08).
6. Confirm daily backup scheduling and 12-month audit retention in deployment.

### Conclusion

Use:

> The project produced a working role-based maize disease diagnostic system with a measured classifier, a tested database and application, and a measured response-time result. It has not yet demonstrated field usability or local-field model performance in Liwaladzi. Those measurements, together with remediation of the data-split defects, determine whether the remaining objectives are fully met.

## 6. Do not fabricate

- H3/SUS/user acceptance: no participant data in this evidence set.
- Android/field use: only desktop/360 px Chrome screenshots exist.
- 35 concurrent sessions: not load tested.
- 95% uptime: no monitoring period.
- HTTPS production deployment: configuration exists, but no deployment evidence.
- 12-month log retention: append-only and chain verification tested; retention schedule not proven.
- Saved model epoch: no training log; D-08 stays “to verify”.
- Exact ground-truth equality in TC-09 route test: use Chapter 7 model metrics for accuracy claims.
