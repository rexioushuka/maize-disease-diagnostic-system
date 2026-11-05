"""First-run / reference-data bootstrapping.

The control vocabulary grows between releases, so missing event types and
reference rows are inserted idempotently at start-up.
"""

from .db import get_db

EXTRA_EVENT_TYPES = [
    (17, "PASSWORD_CHANGE", "AUTHENTICATION", "User changed own password"),
    (18, "PASSWORD_RESET", "ADMINISTRATION", "Administrator reset a password"),
    (19, "PASSWORD_RESET_REQUESTED", "AUTHENTICATION",
     "User requested a password reset token"),
    (20, "MODEL_OOD", "DIAGNOSIS", "Image rejected as out-of-distribution / low confidence"),
    (21, "AUDIT_VERIFY", "SYSTEM", "Audit hash-chain verification run"),
]


def ensure_reference_data():
    conn = get_db()
    conn.executemany(
        "INSERT OR IGNORE INTO event_type "
        "(event_type_id, event_code, category, description) VALUES (?, ?, ?, ?);",
        EXTRA_EVENT_TYPES,
    )
    conn.commit()
