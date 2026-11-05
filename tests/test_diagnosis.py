"""Diagnosis pipeline, OOD handling, upload validation and scoping tests."""

import io

from PIL import Image

from conftest import csrf


def _png_bytes(size=(64, 64), color=(40, 120, 40)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    buf.seek(0)
    return buf


def test_upload_rejects_wrong_extension(client, login):
    login("farmer")
    token = csrf(client, "/farmer/dashboard")
    resp = client.post(
        "/api/v1/predict",
        data={"file": (io.BytesIO(b"hello"), "x.gif"), "section_id": "1",
              "_csrf_token": token},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_upload_rejects_spoofed_type(client, login):
    login("farmer")
    token = csrf(client, "/farmer/dashboard")
    resp = client.post(
        "/api/v1/predict",
        data={"file": (io.BytesIO(b"not an image"), "x.png"), "section_id": "1",
              "_csrf_token": token},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_predict_ok_with_stub_model(client, login):
    login("farmer")
    token = csrf(client, "/farmer/dashboard")
    resp = client.post(
        "/api/v1/predict",
        data={"file": (_png_bytes(), "leaf.png"), "section_id": "1",
              "_csrf_token": token},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert "is_ood" in body["diagnosis"]
    assert body["diagnosis"]["probabilities"]


def test_diagnosis_persisted_and_history_visible(app, client, login):
    login("farmer")
    token = csrf(client, "/farmer/dashboard")
    client.post("/farmer/dashboard",
                data={"file": (_png_bytes(), "leaf.png"), "section_id": "1",
                      "_csrf_token": token},
                content_type="multipart/form-data")
    with app.app_context():
        from msds.db import get_db
        count = get_db().execute("SELECT COUNT(*) FROM diagnosis;").fetchone()[0]
        assert count >= 1


def test_classify_flags_ood():
    from msds.services.diagnosis import classify
    cfg = {"CONFIDENCE_THRESHOLD": 0.70, "OOD_ENTROPY_THRESHOLD": 1.10,
           "OOD_MARGIN_THRESHOLD": 0.15}
    flat = [0.25, 0.25, 0.25, 0.25]
    decision = classify(flat, cfg)
    assert decision["is_ood"] is True

    confident = [0.02, 0.95, 0.02, 0.01]
    decision = classify(confident, cfg)
    assert decision["is_ood"] is False
    assert decision["top"] == 1


def test_officer_scope_blocks_other_sections(app, client, login):
    # Insert a diagnosis in a section the officer is NOT scoped to (section 2).
    with app.app_context():
        from msds.db import get_db
        db = get_db()
        farmer = db.execute("SELECT user_id FROM app_user WHERE username='farmer';"
                            ).fetchone()["user_id"]
        db.execute(
            "INSERT INTO diagnosis (user_id, class_id, section_id, image_sha256, "
            "model_version, inference_ms) VALUES (?,1,2,'abc','v1',5);", (farmer,))
        db.commit()
    login("officer")
    resp = client.get("/officer/dashboard?section_id=2")
    # Officer is scoped to section 1, so section 2 rows must not appear.
    assert resp.status_code == 200
    assert "abc" not in resp.get_data(as_text=True)


def test_audit_chain_verifies(app, client, login):
    login("admin")
    with app.app_context():
        from msds.db import get_db
        from msds.services.audit import verify_chain
        ok, bad = verify_chain(get_db())
        assert ok is True, bad


def test_audit_chain_detects_tampering(app):
    with app.app_context():
        from msds.db import get_db
        from msds.services.audit import log_event, verify_chain
        log_event("LOGIN_SUCCESS", "SUCCESS", user_id=1, detail="a")
        db = get_db()
        # Bypass the trigger by temporarily dropping it, then tamper.
        db.execute("DROP TRIGGER system_log_no_update;")
        db.execute("UPDATE system_log SET detail='tampered' WHERE detail='a';")
        db.commit()
        ok, _ = verify_chain(db)
        assert ok is False
