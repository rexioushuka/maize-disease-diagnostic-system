"""Farmer diagnosis routes (HTML + JSON)."""

from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template, request, session,
    url_for,
)

from ..db import get_db
from ..security_core import allow_request, role_required, t
from ..services.audit import log_event
from ..services.diagnosis import DiagnosisError, run_diagnosis
from ..services.labels import class_label

bp = Blueprint("farmer", __name__)


def _user_section_id(conn, user_id):
    row = conn.execute(
        "SELECT section_id FROM app_user WHERE user_id = ?;", (user_id,)
    ).fetchone()
    return row["section_id"] if row else None


def _user_history(conn, user_id):
    return conn.execute(
        """
        SELECT d.*, c.class_name, c.display_name, p.probability AS confidence
        FROM diagnosis d
        JOIN disease_class c ON d.class_id = c.class_id
        JOIN diagnosis_probability p
          ON d.diagnosis_id = p.diagnosis_id AND d.class_id = p.class_id
        WHERE d.user_id = ? AND d.saved_to_history = 1
        ORDER BY d.created_at DESC;
        """,
        (user_id,),
    ).fetchall()


@bp.route("/farmer/dashboard", methods=["GET", "POST"])
@role_required("FARMER", "EXTENSION_OFFICER", "ADMIN")
def dashboard():
    from flask import current_app

    lang = session.get("lang", "en")
    conn = get_db()

    if request.method == "POST":
        if not allow_request(
            "diagnose", current_app.config["DIAGNOSIS_RATE_LIMIT"],
            current_app.config["DIAGNOSIS_RATE_WINDOW"], session["user_id"],
        ):
            log_event("UNAUTHORIZED_ATTEMPT", "DENIED",
                      user_id=session["user_id"], detail="Diagnosis rate limit exceeded")
            abort(429)

        try:
            result = run_diagnosis(
                request.files.get("file"), request.form.get("section_id"),
                session["user_id"], lang,
            )
        except DiagnosisError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("farmer.dashboard"))
        except Exception as exc:  # pragma: no cover - defensive
            current_app.logger.exception("Diagnosis failed")
            log_event("SYSTEM_ERROR", "FAILURE", user_id=session.get("user_id"),
                      detail=str(exc))
            flash(t("msg_diagnosis_error"), "danger")
            return redirect(url_for("farmer.dashboard"))

        if result["is_ood"]:
            log_event("MODEL_OOD", "SUCCESS", user_id=session["user_id"],
                      diagnosis_id=result["diagnosis_id"],
                      detail=f"entropy={result['entropy']}, margin={result['margin']}")

        sections = conn.execute(
            "SELECT * FROM village_section ORDER BY section_name;"
        ).fetchall()
        return render_template(
            "farmer.html", sections=sections,
            history=_user_history(conn, session["user_id"]), result=result,
            user_section_id=_user_section_id(conn, session["user_id"]),
        )

    sections = conn.execute(
        "SELECT * FROM village_section ORDER BY section_name;"
    ).fetchall()
    return render_template(
        "farmer.html", sections=sections,
        history=_user_history(conn, session["user_id"]),
        user_section_id=_user_section_id(conn, session["user_id"]),
    )


@bp.route("/predict", methods=["POST"])
@bp.route("/api/v1/predict", methods=["POST"])
@role_required("FARMER", "EXTENSION_OFFICER", "ADMIN")
def predict_api():
    """Versioned JSON diagnosis endpoint (QUA-12)."""
    from flask import current_app

    if not allow_request(
        "diagnose", current_app.config["DIAGNOSIS_RATE_LIMIT"],
        current_app.config["DIAGNOSIS_RATE_WINDOW"], session["user_id"],
    ):
        return jsonify(success=False, error=t("msg_rate_limited")), 429

    file = request.files.get("image") or request.files.get("file")
    try:
        result = run_diagnosis(
            file, request.form.get("section_id"), session["user_id"],
            session.get("lang", "en"),
        )
    except DiagnosisError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except Exception as exc:  # pragma: no cover - defensive
        current_app.logger.exception("Prediction API failed")
        log_event("SYSTEM_ERROR", "FAILURE", user_id=session.get("user_id"),
                  detail=str(exc))
        return jsonify(success=False, error=t("msg_diagnosis_error")), 500

    status = t("farmer.healthy") if result["class_name"] == "Healthy" \
        else t("farmer.disease_detected")
    if result["is_ood"]:
        status = t("farmer.inconclusive")
    advisory = result["advisory"] or {}
    return jsonify(
        success=True,
        diagnosis={
            "id": result["diagnosis_id"],
            "disease": class_label(result["class_name"]),
            "class_name": result["class_name"],
            "status": status,
            "confidence_score": f"{result['confidence']}%",
            "confidence": result["confidence"],
            "low_confidence": result["low_confidence"],
            "is_ood": result["is_ood"],
            "entropy": result["entropy"],
            "margin": result["margin"],
            "description": advisory.get("description", ""),
            "recommended_action": advisory.get("action_text", ""),
            "source": advisory.get("source_ref", ""),
            "probabilities": result["probabilities"],
            "total_ms": result["total_ms"],
        },
    )
