"""Extension officer dashboards and CSV export (scoped per officer - SEC-11)."""

import csv
import io
from datetime import datetime, timezone

from flask import Blueprint, Response, render_template, request, session

from ..db import get_db
from ..security_core import role_required, t
from ..services.audit import log_event
from ..services.labels import class_label

bp = Blueprint("officer", __name__)


def _officer_sections(conn, user_id):
    """Return the set of section_id an officer may see, or None for admin."""
    if session.get("role_name") == "ADMIN":
        return None
    rows = conn.execute(
        "SELECT section_id FROM officer_section WHERE user_id = ?;", (user_id,)
    ).fetchall()
    return {r["section_id"] for r in rows}


def _parse_filters(args, allowed_sections):
    where = ["1=1"]
    params = []
    filters = {
        "date_from": (args.get("date_from") or "").strip(),
        "date_to": (args.get("date_to") or "").strip(),
        "class_id": (args.get("class_id") or "").strip(),
        "section_id": (args.get("section_id") or "").strip(),
    }
    for key, column in (("date_from", ">="), ("date_to", "<=")):
        value = filters[key]
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                filters[key] = ""
                continue
            where.append(f"date(d.created_at) {column} date(?)")
            params.append(value)
    if filters["class_id"].isdigit():
        where.append("d.class_id = ?")
        params.append(int(filters["class_id"]))
    else:
        filters["class_id"] = ""
    if filters["section_id"].isdigit():
        where.append("d.section_id = ?")
        params.append(int(filters["section_id"]))
    else:
        filters["section_id"] = ""

    # Enforce scope in SQL, not just in the UI (SEC-11).
    if allowed_sections is not None:
        if not allowed_sections:
            where.append("1=0")
        else:
            placeholders = ",".join("?" for _ in allowed_sections)
            where.append(f"d.section_id IN ({placeholders})")
            params.extend(sorted(allowed_sections))

    return " AND ".join(where), params, filters


@bp.route("/officer/dashboard")
@role_required("EXTENSION_OFFICER", "ADMIN")
def dashboard():
    conn = get_db()
    allowed = _officer_sections(conn, session["user_id"])
    where, params, filters = _parse_filters(request.args, allowed)
    cursor = conn.cursor()

    total_scans = cursor.execute(
        f"SELECT COUNT(*) FROM diagnosis d WHERE {where};", params
    ).fetchone()[0]
    disease_filter = ("d.class_id IN (SELECT class_id FROM disease_class "
                      "WHERE is_disease = 1)")
    healthy_filter = ("d.class_id IN (SELECT class_id FROM disease_class "
                      "WHERE is_disease = 0)")
    outbreaks = cursor.execute(
        f"SELECT COUNT(*) FROM diagnosis d WHERE {where} AND {disease_filter};",
        params).fetchone()[0]
    healthy_cases = cursor.execute(
        f"SELECT COUNT(*) FROM diagnosis d WHERE {where} AND {healthy_filter};",
        params).fetchone()[0]

    class_counts = cursor.execute(
        f"""
        SELECT c.class_id, c.class_name, c.display_name,
               COUNT(d.diagnosis_id) AS count
        FROM disease_class c
        LEFT JOIN diagnosis d ON c.class_id = d.class_id AND ({where})
        GROUP BY c.class_id ORDER BY c.class_id;
        """,
        params,
    ).fetchall()

    weekly_trend = cursor.execute(
        f"""
        SELECT strftime('%Y-%W', d.created_at) AS week, COUNT(*) AS count
        FROM diagnosis d WHERE {where}
        GROUP BY week ORDER BY week DESC LIMIT 8;
        """,
        params,
    ).fetchall()
    weekly_trend = list(reversed(weekly_trend))

    by_section = cursor.execute(
        f"""
        SELECT COALESCE(s.section_name, 'Unspecified') AS section_name,
               COUNT(*) AS count,
               SUM(CASE WHEN {disease_filter} THEN 1 ELSE 0 END) AS disease_count
        FROM diagnosis d
        LEFT JOIN village_section s ON d.section_id = s.section_id
        WHERE {where}
        GROUP BY s.section_id ORDER BY count DESC;
        """,
        params,
    ).fetchall()

    recent_logs = cursor.execute(
        f"""
        SELECT d.diagnosis_id, d.created_at, c.class_name, c.display_name,
               p.probability AS confidence, s.section_name
        FROM diagnosis d
        JOIN disease_class c ON d.class_id = c.class_id
        JOIN diagnosis_probability p
          ON d.diagnosis_id = p.diagnosis_id AND d.class_id = p.class_id
        LEFT JOIN village_section s ON d.section_id = s.section_id
        WHERE {where}
        ORDER BY d.created_at DESC LIMIT 25;
        """,
        params,
    ).fetchall()

    if allowed is None:
        sections = cursor.execute(
            "SELECT * FROM village_section ORDER BY section_name;").fetchall()
    else:
        sections = cursor.execute(
            "SELECT * FROM village_section WHERE section_id IN "
            f"({','.join('?' for _ in allowed) or 'NULL'}) ORDER BY section_name;",
            sorted(allowed),
        ).fetchall()
    classes = cursor.execute(
        "SELECT * FROM disease_class ORDER BY class_id;").fetchall()

    log_event("DASHBOARD_VIEW", "SUCCESS", user_id=session["user_id"],
              detail=f"Filters: {filters}")

    return render_template(
        "officer.html", total_scans=total_scans, outbreaks=outbreaks,
        healthy_cases=healthy_cases, class_counts=class_counts,
        weekly_trend=weekly_trend, by_section=by_section,
        recent_logs=recent_logs, sections=sections, classes=classes,
        filters=filters,
        no_sections=allowed is not None and not allowed,
    )


@bp.route("/officer/export")
@role_required("EXTENSION_OFFICER", "ADMIN")
def export_report():
    conn = get_db()
    allowed = _officer_sections(conn, session["user_id"])
    where, params, filters = _parse_filters(request.args, allowed)
    rows = conn.execute(
        f"""
        SELECT d.diagnosis_id, d.created_at, c.class_name, c.display_name,
               p.probability AS confidence, s.section_name, d.model_version
        FROM diagnosis d
        JOIN disease_class c ON d.class_id = c.class_id
        JOIN diagnosis_probability p
          ON d.diagnosis_id = p.diagnosis_id AND d.class_id = p.class_id
        LEFT JOIN village_section s ON d.section_id = s.section_id
        WHERE {where}
        ORDER BY d.created_at DESC;
        """,
        params,
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        t("export.id"), t("export.datetime"), t("export.class"),
        t("export.confidence"), t("export.section"), t("export.model"),
    ])
    for row in rows:
        writer.writerow([
            row["diagnosis_id"], row["created_at"],
            class_label(row["class_name"]),
            f"{row['confidence'] * 100:.2f}%",
            row["section_name"] or t("common.unspecified"), row["model_version"],
        ])

    log_event("REPORT_EXPORT", "SUCCESS", user_id=session["user_id"],
              detail=f"{len(rows)} rows; filters={filters}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Response(
        output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="maize_diagnoses_{stamp}.csv"'},
    )
