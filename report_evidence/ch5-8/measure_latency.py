import csv
import io
import json
import math
import os
import platform
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import requests
from PIL import Image
from werkzeug.security import generate_password_hash

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("EVIDENCE_PORT", "5057"))
BASE_URL = f"http://127.0.0.1:{PORT}"
PASSWORD = "StrongPass123!"
RUNS = 30
MAX_IMAGE_BYTES = 500 * 1024


def _seed_database(db_path, work_dir):
    conn = sqlite3.connect(db_path)
    with open(os.path.join(BASE, "maize_schema.sql"), encoding="utf-8") as handle:
        conn.executescript(handle.read())
    conn.execute(
        "INSERT INTO village_section (section_name, epa_name) "
        "VALUES ('Nzumala', 'Liwaladzi');"
    )
    password_hash = generate_password_hash(PASSWORD, method="pbkdf2:sha256")
    for index in (1, 2):
        conn.execute(
            "INSERT INTO app_user (full_name, username, email, password_hash, role_id, "
            "section_id) VALUES (?, ?, ?, ?, 1, 1);",
            (f"Performance Farmer {index}", f"perfuser{index}",
             f"perfuser{index}@example.com", password_hash),
        )
    for class_id in range(4):
        for language in ("en", "ny"):
            conn.execute(
                "INSERT INTO advisory (class_id, language_code, status_text, "
                "description, action_text, source_ref) VALUES (?, ?, ?, ?, ?, ?);",
                (class_id, language, "Status", "Description", "Action", "IPM source"),
            )
    conn.commit()
    conn.close()


def _image_payloads():
    root = os.path.join(BASE, "data", "processed", "test")
    paths = []
    for folder, _, names in os.walk(root):
        for name in names:
            if name.lower().endswith((".jpg", ".jpeg", ".png")):
                paths.append(os.path.join(folder, name))
    paths.sort()
    step = max(1, len(paths) // RUNS)
    selected = [paths[index * step] for index in range(RUNS)]
    payloads = []
    for path in selected:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((1024, 1024))
            for quality in (88, 80, 72, 64, 56, 48, 40):
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality)
                data = output.getvalue()
                if len(data) <= MAX_IMAGE_BYTES:
                    break
        payloads.append((os.path.basename(path), data))
    return payloads


def _csrf(session, path):
    html = session.get(BASE_URL + path, timeout=30).text
    match = re.search(r'name="_csrf_token"\s+value="([^"]+)"', html)
    if not match:
        match = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    if not match:
        raise RuntimeError(f"No CSRF token found on {path}")
    return match.group(1)


def _signed_in_session(username):
    session = requests.Session()
    token = _csrf(session, "/login")
    response = session.post(
        BASE_URL + "/login",
        data={"login_input": username, "password": PASSWORD, "_csrf_token": token},
        allow_redirects=False,
        timeout=30,
    )
    if response.status_code != 302:
        raise RuntimeError(f"Login failed for {username}: {response.status_code}")
    return session, _csrf(session, "/farmer/dashboard")


def _wait_for_health():
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        try:
            response = requests.get(BASE_URL + "/health", timeout=5)
            if response.status_code == 200 and response.json().get("model_loaded"):
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise RuntimeError("Application did not become healthy with the model loaded")


def main():
    mapping_path = os.path.join(BASE, "models", "class_mapping.json")
    with open(mapping_path, encoding="utf-8") as handle:
        mapping = json.load(handle)

    work_dir = tempfile.mkdtemp(prefix="maize_latency_")
    db_path = os.path.join(work_dir, "latency.db")
    upload_dir = os.path.join(work_dir, "uploads")
    _seed_database(db_path, work_dir)

    env = os.environ.copy()
    env.update({
        "APP_CONFIG": "development",
        "LOAD_MODEL": "1",
        "MODEL_HASH_VERIFY": "1",
        "DB_PATH": db_path,
        "UPLOAD_FOLDER": upload_dir,
        "SECRET_KEY": os.urandom(32).hex(),
        "AUDIT_HMAC_KEY": os.urandom(32).hex(),
        "HOST": "127.0.0.1",
        "PORT": str(PORT),
        "PREFERRED_URL_SCHEME": "http",
    })
    for key in ("BREVO_API_KEY", "BREVO_SENDER_EMAIL", "BREVO_SENDER_NAME",
                "BREVO_SMS_SENDER", "BREVO_SMTP_LOGIN", "BREVO_SMTP_KEY"):
        env[key] = ""

    server_log = os.path.join(OUTPUT_DIR, "latency_server.log")
    records = []
    with open(server_log, "w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            [sys.executable, os.path.join(BASE, "app.py")],
            cwd=BASE, env=env, stdout=log_handle, stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_health()
            sessions = [_signed_in_session("perfuser1"),
                        _signed_in_session("perfuser2")]
            payloads = _image_payloads()
            for index, (name, data) in enumerate(payloads, start=1):
                session, token = sessions[(index - 1) % len(sessions)]
                started = time.perf_counter()
                response = session.post(
                    BASE_URL + "/api/v1/predict",
                    data={"section_id": "1", "_csrf_token": token},
                    files={"image": (name, data, "image/jpeg")},
                    timeout=60,
                )
                wall_ms = (time.perf_counter() - started) * 1000
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Run {index} returned {response.status_code}: {response.text[:200]}"
                    )
                body = response.json()
                records.append({
                    "run": index,
                    "user": "perfuser1" if index % 2 == 1 else "perfuser2",
                    "image": name,
                    "image_bytes": len(data),
                    "wall_ms": round(wall_ms, 1),
                    "server_total_ms": body["diagnosis"]["total_ms"],
                    "server_inference_ms": body["diagnosis"].get("inference_ms"),
                    "class_name": body["diagnosis"]["class_name"],
                    "confidence": body["diagnosis"]["confidence"],
                    "success": body["success"],
                })
                print(f"run {index:02d}: {wall_ms:.0f} ms")
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()

    walls = [item["wall_ms"] for item in records]
    ordered = sorted(walls)
    p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
    summary = {
        "runs": len(records),
        "successes": sum(item["success"] for item in records),
        "median_wall_ms": round(statistics.median(walls), 1),
        "mean_wall_ms": round(statistics.mean(walls), 1),
        "min_wall_ms": round(min(walls), 1),
        "max_wall_ms": round(max(walls), 1),
        "p95_wall_ms": round(p95, 1),
        "median_server_total_ms": round(statistics.median(
            [item["server_total_ms"] for item in records]), 1),
        "target_median_ms": 5000,
        "target_p95_ms": 10000,
        "median_target_met": statistics.median(walls) <= 5000,
        "p95_target_met": p95 <= 10000,
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "30 consecutive POST /api/v1/predict requests over HTTP "
                  "to python app.py with the real MobileNetV2 model",
        "command": "python report_evidence/ch5-8/measure_latency.py",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "model_version": mapping["model_version"],
        "model_sha256": mapping["sha256"],
        "max_upload_bytes_observed": max(item["image_bytes"] for item in records),
        "server_log": "report_evidence/ch5-8/latency_server.log",
        "summary": summary,
        "runs": records,
    }
    json_path = os.path.join(OUTPUT_DIR, "latency_30_runs.json")
    csv_path = os.path.join(OUTPUT_DIR, "latency_30_runs.csv")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
