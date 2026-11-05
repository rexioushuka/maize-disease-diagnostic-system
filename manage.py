"""Operational CLI: purge-images, verify-audit, create-admin, rehash-model."""

import argparse
import json
import os
import sys

from msds import create_app


def _app(config=None):
    return create_app(config or os.environ.get("APP_CONFIG", "development"))


def cmd_purge_images(args):
    app = _app()
    from msds.services.diagnosis import purge_expired_images

    with app.app_context():
        removed = purge_expired_images(days=args.days, dry_run=args.dry_run)
    print(f"{'Would remove' if args.dry_run else 'Removed'} {removed} image(s)")


def cmd_verify_audit(args):
    app = _app()
    from msds.services.audit import verify_chain

    with app.app_context():
        ok, bad = verify_chain()
    print(json.dumps({"ok": ok, "first_bad_log_id": bad}))
    return 0 if ok else 1


def cmd_create_admin(args):
    app = _app()
    from msds.db import get_db
    from werkzeug.security import generate_password_hash

    with app.app_context():
        conn = get_db()
        conn.execute(
            "INSERT INTO app_user (full_name, username, email, password_hash, "
            "role_id, must_change_password) VALUES (?, ?, ?, ?, 3, 1);",
            (args.name, args.username, args.email,
             generate_password_hash(args.password, method="pbkdf2:sha256")),
        )
        conn.commit()
    print(f"Created admin {args.username}")


def cmd_rehash_model(args):
    """Recompute and store the model SHA-256 in class_mapping.json."""
    import hashlib

    base = os.path.dirname(os.path.abspath(__file__))
    mapping_path = os.path.join(base, "models", "class_mapping.json")
    model_path = os.path.join(base, "models", "mobilenetv2_maize.keras")
    h = hashlib.sha256(open(model_path, "rb").read()).hexdigest()
    with open(mapping_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data["sha256"] = h
    with open(mapping_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    print(f"Updated sha256 to {h}")


def main():
    parser = argparse.ArgumentParser(description="Maize system operations")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("purge-images", help="delete evidence images past retention")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_purge_images)

    p = sub.add_parser("verify-audit", help="verify the audit hash chain")
    p.set_defaults(func=cmd_verify_audit)

    p = sub.add_parser("create-admin", help="create an administrator")
    p.add_argument("--username", required=True)
    p.add_argument("--name", default="System Administrator")
    p.add_argument("--email", required=True)
    p.add_argument("--password", required=True)
    p.set_defaults(func=cmd_create_admin)

    p = sub.add_parser("rehash-model", help="refresh the model SHA-256")
    p.set_defaults(func=cmd_rehash_model)

    args = parser.parse_args()
    rc = args.func(args)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
