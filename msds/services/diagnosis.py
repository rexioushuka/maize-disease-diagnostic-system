"""Diagnosis service: validation, inference, OOD/abstain, persistence.

Fixes:
* SEC-09  evidence images are stored in per-user/per-day folders and are
          subject to a retention purge job.
* SEC-19  uploads are streamed from a temp file with a hard cap before any
          decoding; a unique storage name is used (no silent overwrite).
* SEC-20  entropy + margin based abstain/OOD handling; low-confidence and OOD
          results are not recorded as disease cases.
* QUA-12  one service function shared by the HTML and JSON controllers.
"""

import hashlib
import io
import math
import os
import tempfile
import time
import uuid
from datetime import timedelta

import numpy as np
from PIL import Image, UnidentifiedImageError

from flask import current_app

from ..db import get_db
from ..security_core import utc_stamp, utcnow
from .audit import log_event


class DiagnosisError(ValueError):
    """Client-facing validation error (maps to HTTP 400)."""


def _t(key, **kwargs):
    from ..security_core import t
    return t(key, **kwargs)


def _spool_upload(file_storage):
    """Stream the upload to a capped temp file; return (path, sha256, size)."""
    if file_storage is None or not file_storage.filename:
        raise DiagnosisError(_t("msg_no_file"))
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        raise DiagnosisError(_t("msg_invalid_file_type"))

    max_bytes = current_app.config["MAX_UPLOAD_BYTES"]
    h = hashlib.sha256()
    total = 0
    fd, tmp_path = tempfile.mkstemp(prefix="upload_", suffix=".bin")
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = file_storage.stream.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise DiagnosisError(_t("msg_file_too_large"))
                h.update(chunk)
                out.write(chunk)
        if total == 0:
            raise DiagnosisError(_t("msg_no_file"))
        return tmp_path, h.hexdigest(), total
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _decode_and_validate(tmp_path):
    with open(tmp_path, "rb") as fh:
        head = fh.read(8)
    is_jpeg = head.startswith(b"\xff\xd8\xff")
    is_png = head.startswith(b"\x89PNG\r\n\x1a\n")
    if not (is_jpeg or is_png):
        raise DiagnosisError(_t("msg_invalid_file_type"))
    try:
        with Image.open(tmp_path) as probe:
            probe.verify()
        image = Image.open(tmp_path).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        raise DiagnosisError(_t("msg_image_corrupt"))
    return image


def _persist_evidence(image, user_id, img_sha256):
    root = current_app.config["UPLOAD_FOLDER"]
    day = utcnow().strftime("%Y%m%d")
    folder = os.path.join(root, f"u{int(user_id):04d}", day)
    os.makedirs(folder, exist_ok=True)
    # Decorative unique name prevents one farmer's upload overwriting another (SEC-19).
    filename = f"{img_sha256[:16]}_{uuid.uuid4().hex[:8]}.jpg"
    path = os.path.join(folder, filename)
    stored = image.copy()
    stored.thumbnail((1024, 1024))
    stored.save(path, "JPEG", quality=88)
    return path


def _valid_section_id(raw_section_id):
    if raw_section_id in (None, ""):
        return None
    try:
        section_id = int(raw_section_id)
    except (TypeError, ValueError):
        raise DiagnosisError(_t("msg_invalid_section"))
    conn = get_db()
    row = conn.execute(
        "SELECT section_id FROM village_section WHERE section_id = ?;",
        (section_id,),
    ).fetchone()
    if not row:
        raise DiagnosisError(_t("msg_invalid_section"))
    return section_id


def _normalise(predictions):
    p = np.asarray(predictions, dtype=np.float64)
    p = np.clip(p, 1e-9, 1.0)
    return p / p.sum()


def classify(predictions, config):
    """Return a decision dict including OOD/abstain flag (SEC-20)."""
    p = _normalise(predictions)
    order = np.argsort(p)[::-1]
    top = int(order[0])
    second = float(p[order[1]]) if len(order) > 1 else 0.0
    confidence = float(p[top])
    margin = confidence - second
    entropy = float(-(p * np.log(p)).sum())
    is_ood = (
        confidence < config["CONFIDENCE_THRESHOLD"]
        or entropy > config["OOD_ENTROPY_THRESHOLD"]
        or margin < config["OOD_MARGIN_THRESHOLD"]
    )
    return {"top": top, "confidence": confidence, "margin": margin,
            "entropy": entropy, "is_ood": is_ood}


def run_diagnosis(file_storage, section_id, user_id, language_code):
    """Validate, classify, persist and return one diagnosis (FR-07..FR-12)."""
    start_time = time.time()
    tmp_path, img_sha256, _size = _spool_upload(file_storage)
    try:
        image = _decode_and_validate(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    section_id = _valid_section_id(section_id)

    model = current_app.extensions.get("maize_model")
    class_names = current_app.config["CLASS_NAMES"]
    if model is None:
        raise DiagnosisError(_t("msg_diagnosis_error"))

    file_path = _persist_evidence(image, user_id, img_sha256)

    input_size = current_app.config["MODEL_INPUT_SIZE"]
    model_input = image.resize(input_size)
    img_array = np.asarray(model_input, dtype=np.float32) / 255.0
    img_tensor = np.expand_dims(img_array, axis=0)

    infer_start = time.time()
    predictions = model.predict(img_tensor, verbose=0)[0]
    infer_ms = int((time.time() - infer_start) * 1000)

    decision = classify(predictions, current_app.config)
    predicted_class_id = decision["top"]
    top_confidence = decision["confidence"]
    total_ms = int((time.time() - start_time) * 1000)

    conn = get_db()
    cursor = conn.cursor()
    # Low-confidence/OOD results are recorded with saved_to_history=0 so they
    # are not reported as confirmed disease cases (SEC-20).
    save_to_history = 0 if decision["is_ood"] else 1
    cursor.execute(
        """
        INSERT INTO diagnosis
            (user_id, class_id, section_id, image_sha256, image_path,
             model_version, inference_ms, total_ms, saved_to_history)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (user_id, predicted_class_id, section_id, img_sha256, file_path,
         current_app.config["MODEL_VERSION"], infer_ms, total_ms, save_to_history),
    )
    diagnosis_id = cursor.lastrowid

    for cls_id, probability in enumerate(predictions):
        cursor.execute(
            "INSERT INTO diagnosis_probability (diagnosis_id, class_id, probability) "
            "VALUES (?, ?, ?);",
            (diagnosis_id, cls_id, float(probability)),
        )
    conn.commit()

    advisory = cursor.execute(
        """
        SELECT a.*, c.display_name
        FROM advisory a JOIN disease_class c ON a.class_id = c.class_id
        WHERE a.class_id = ? AND a.language_code = ?;
        """,
        (predicted_class_id, language_code),
    ).fetchone()
    if advisory is None:
        advisory = cursor.execute(
            """
            SELECT a.*, c.display_name
            FROM advisory a JOIN disease_class c ON a.class_id = c.class_id
            WHERE a.class_id = ? AND a.language_code = 'en';
            """,
            (predicted_class_id,),
        ).fetchone()

    class_rows = cursor.execute(
        "SELECT class_id, display_name FROM disease_class ORDER BY class_id;"
    ).fetchall()
    display_names = {row["class_id"]: row["display_name"] for row in class_rows}

    probabilities = [
        {
            "class_name": class_names[idx],
            "display_name": display_names.get(idx, class_names[idx]),
            "probability": round(float(probability) * 100, 2),
        }
        for idx, probability in enumerate(predictions)
    ]

    log_event(
        "MODEL_INFERENCE",
        "SUCCESS",
        user_id=user_id,
        diagnosis_id=diagnosis_id,
        detail=(f"Class: {class_names[predicted_class_id]}, "
                f"Conf: {top_confidence:.4f}, OOD: {decision['is_ood']}"),
    )

    return {
        "diagnosis_id": diagnosis_id,
        "class_name": class_names[predicted_class_id],
        "display_name": display_names.get(predicted_class_id,
                                          class_names[predicted_class_id]),
        "confidence": round(top_confidence * 100, 2),
        "low_confidence": top_confidence < current_app.config["CONFIDENCE_THRESHOLD"],
        "is_ood": decision["is_ood"],
        "margin": round(decision["margin"] * 100, 2),
        "entropy": round(decision["entropy"], 4),
        "advisory": dict(advisory) if advisory else None,
        "probabilities": probabilities,
        "inference_ms": infer_ms,
        "total_ms": total_ms,
        "timestamp": utc_stamp(),
    }


def purge_expired_images(days=None, dry_run=False):
    """Delete evidence images older than the retention window (SEC-09)."""
    days = days if days is not None else current_app.config["IMAGE_RETENTION_DAYS"]
    cutoff = utcnow() - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    root = current_app.config["UPLOAD_FOLDER"]
    removed = 0
    if not os.path.isdir(root):
        return 0
    conn = get_db()
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                if os.path.getmtime(path) < cutoff_ts:
                    if not dry_run:
                        os.remove(path)
                        conn.execute(
                            "UPDATE diagnosis SET image_path = NULL WHERE image_path = ?;",
                            (path,),
                        )
                    removed += 1
            except OSError:
                continue
    if not dry_run:
        conn.commit()
    return removed
