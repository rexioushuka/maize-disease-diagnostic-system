import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from PIL import Image
from werkzeug.security import generate_password_hash

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENSHOT_DIR = os.path.join(OUTPUT_DIR, "screenshots")
HTML_DIR = os.path.join(SCREENSHOT_DIR, "html")
PORT = int(os.environ.get("EVIDENCE_SCREEN_PORT", "5058"))
SNAPSHOT_PORT = PORT + 1
BASE_URL = f"http://127.0.0.1:{PORT}"
SNAPSHOT_URL = f"http://127.0.0.1:{SNAPSHOT_PORT}"
PASSWORD = "StrongPass123!"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

os.makedirs(HTML_DIR, exist_ok=True)


class SnapshotHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.path.startswith("/snapshot/"):
            self.send_error(404)
            return
        name = os.path.basename(self.path[len("/snapshot/"):])
        path = os.path.join(HTML_DIR, name)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as handle:
            data = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


def _seed_database(db_path):
    conn = sqlite3.connect(db_path)
    with open(os.path.join(BASE, "maize_schema.sql"), encoding="utf-8") as handle:
        conn.executescript(handle.read())
    for name in ("Nzumala", "Gowoka", "Kambwiri", "Zunga", "Nsindwa",
                 "Kautaka", "Liwaladzi Central"):
        conn.execute(
            "INSERT INTO village_section (section_name, epa_name) "
            "VALUES (?, 'Liwaladzi');", (name,)
        )
    password_hash = generate_password_hash(PASSWORD, method="pbkdf2:sha256")
    admin_id = conn.execute(
        "INSERT INTO app_user (full_name, username, email, password_hash, role_id) "
        "VALUES ('Admin User', 'admin', 'admin@example.com', ?, 3);",
        (password_hash,),
    ).lastrowid
    conn.execute(
        "INSERT INTO app_user (full_name, username, email, password_hash, role_id, "
        "section_id) VALUES ('Demo Farmer', 'farmer', 'f@example.com', ?, 1, 1);",
        (password_hash,),
    )
    officer_id = conn.execute(
        "INSERT INTO app_user (full_name, username, email, password_hash, role_id, "
        "section_id, created_by) VALUES ('Extension Officer', 'officer', "
        "'o@example.com', ?, 2, 1, ?);", (password_hash, admin_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO officer_section (user_id, section_id) VALUES (?, 1);",
        (officer_id,),
    )
    sys.path.insert(0, BASE)
    from init_db import _advisories
    conn.executemany(
        "INSERT INTO advisory (class_id, language_code, status_text, description, "
        "action_text, source_ref) VALUES (?, ?, ?, ?, ?, ?);",
        _advisories(),
    )
    conn.commit()
    conn.close()


def _sample_image():
    root = os.path.join(BASE, "data", "processed", "test")
    candidates = []
    for folder, _, names in os.walk(root):
        for name in names:
            if name.lower().endswith((".jpg", ".jpeg", ".png")):
                candidates.append(os.path.join(folder, name))
    candidates.sort()
    path = candidates[len(candidates) // 2]
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((1024, 1024))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=82)
        return os.path.basename(path), output.getvalue()


def _csrf(session, path):
    html = session.get(BASE_URL + path, timeout=30).text
    match = re.search(r'name="_csrf_token"\s+value="([^"]+)"', html)
    if not match:
        match = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    if not match:
        raise RuntimeError(f"No CSRF token found on {path}")
    return match.group(1)


def _login(username):
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
    return session


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


def _save_snapshot(name, html):
    html = html.replace('href="/static/', f'href="{BASE_URL}/static/')
    html = html.replace('src="/static/', f'src="{BASE_URL}/static/')
    if name == "S8_admin_system_log":
        script = (
            "<script>window.addEventListener('load',function(){"
            "var h=Array.from(document.querySelectorAll('h5')).find("
            "function(e){return /log/i.test(e.textContent);});"
            "if(h){h.scrollIntoView();}});</script>"
        )
        html = html.replace("</body>", script + "</body>")
    path = os.path.join(HTML_DIR, name + ".html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html)
    return path


def _capture(name, width, height, fragment=""):
    output = os.path.join(SCREENSHOT_DIR, name + ".png")
    profile = tempfile.mkdtemp(prefix="maize_chrome_")
    command = [
        CHROME,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--hide-scrollbars",
        f"--force-device-scale-factor=1",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        "--virtual-time-budget=6000",
        f"--screenshot={output}",
        SNAPSHOT_URL + "/snapshot/" + name + ".html" + fragment,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    if result.returncode != 0 or not os.path.isfile(output):
        raise RuntimeError(f"Chrome failed for {name}: {result.stderr[:500]}")
    if os.path.getsize(output) == 0:
        raise RuntimeError(f"Empty screenshot for {name}")
    return output


def main():
    work_dir = tempfile.mkdtemp(prefix="maize_screenshots_")
    db_path = os.path.join(work_dir, "screenshots.db")
    _seed_database(db_path)

    env = os.environ.copy()
    env.update({
        "APP_CONFIG": "development",
        "LOAD_MODEL": "1",
        "DB_PATH": db_path,
        "UPLOAD_FOLDER": os.path.join(work_dir, "uploads"),
        "SECRET_KEY": os.urandom(32).hex(),
        "AUDIT_HMAC_KEY": os.urandom(32).hex(),
        "HOST": "127.0.0.1",
        "PORT": str(PORT),
        "PREFERRED_URL_SCHEME": "http",
    })
    for key in ("BREVO_API_KEY", "BREVO_SENDER_EMAIL", "BREVO_SENDER_NAME",
                "BREVO_SMS_SENDER", "BREVO_SMTP_LOGIN", "BREVO_SMTP_KEY"):
        env[key] = ""

    snapshot_server = ThreadingHTTPServer(("127.0.0.1", SNAPSHOT_PORT), SnapshotHandler)
    threading.Thread(target=snapshot_server.serve_forever, daemon=True).start()

    captured = []
    server_log = os.path.join(OUTPUT_DIR, "screenshot_server.log")
    with open(server_log, "w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            [sys.executable, os.path.join(BASE, "app.py")],
            cwd=BASE, env=env, stdout=log_handle, stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_health()
            public = requests.Session()
            for name, path in (("S3_sign_in", "/login"),
                               ("S3_sign_up", "/register"),
                               ("S4_officer_activation", "/activate")):
                html = public.get(BASE_URL + path, timeout=30).text
                _save_snapshot(name, html)

            farmer = _login("farmer")
            home = farmer.get(BASE_URL + "/farmer/dashboard", timeout=30).text
            _save_snapshot("S1_farmer_home", home)

            file_name, image_bytes = _sample_image()
            token = _csrf(farmer, "/farmer/dashboard")
            result = farmer.post(
                BASE_URL + "/farmer/dashboard",
                data={"section_id": "1", "_csrf_token": token},
                files={"file": (file_name, image_bytes, "image/jpeg")},
                timeout=60,
            )
            if result.status_code != 200:
                raise RuntimeError(f"Diagnosis page returned {result.status_code}")
            result_html = result.text
            _save_snapshot("S2_diagnosis_result", result_html)

            for second_file in ("MSV", "MLN"):
                root = os.path.join(BASE, "data", "processed", "test", second_file)
                names = sorted(os.listdir(root))
                path = os.path.join(root, names[len(names) // 2])
                with Image.open(path) as image:
                    image = image.convert("RGB")
                    image.thumbnail((800, 800))
                    output = io.BytesIO()
                    image.save(output, format="JPEG", quality=75)
                    data = output.getvalue()
                farmer.post(
                    BASE_URL + "/api/v1/predict",
                    data={"section_id": "1", "_csrf_token": _csrf(
                        farmer, "/farmer/dashboard")},
                    files={"image": (os.path.basename(path), data, "image/jpeg")},
                    timeout=60,
                )

            history = farmer.get(BASE_URL + "/farmer/dashboard", timeout=30).text
            _save_snapshot("S5_farmer_history", history)

            officer = _login("officer")
            officer_html = officer.get(BASE_URL + "/officer/dashboard", timeout=30).text
            _save_snapshot("S6_officer_dashboard", officer_html)

            admin = _login("admin")
            admin_html = admin.get(BASE_URL + "/admin/dashboard", timeout=30).text
            _save_snapshot("S7_admin_users", admin_html)
            _save_snapshot("S8_admin_system_log", admin_html)

            shots = [
                ("S1_farmer_home", 360, 900, ""),
                ("S2_diagnosis_result", 360, 1100, ""),
                ("S3_sign_in", 360, 900, ""),
                ("S3_sign_up", 360, 900, ""),
                ("S4_officer_activation", 360, 900, ""),
                ("S5_farmer_history", 360, 1100, ""),
                ("S6_officer_dashboard", 1366, 1000, ""),
                ("S7_admin_users", 1400, 1000, ""),
                ("S8_admin_system_log", 1400, 1200, ""),
            ]
            for name, width, height, fragment in shots:
                path = _capture(name, width, height, fragment)
                captured.append({
                    "id": name,
                    "file": os.path.relpath(path, BASE).replace("\\", "/"),
                    "width": width,
                    "height": height,
                    "html_snapshot": os.path.relpath(
                        os.path.join(HTML_DIR, name + ".html"), BASE
                    ).replace("\\", "/"),
                })
                print(f"captured {name}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
            snapshot_server.shutdown()

    chrome_version = subprocess.run(
        [CHROME, "--version"], capture_output=True, text=True, timeout=30
    ).stdout.strip()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "Authenticated pages rendered by the running Flask application "
                  "with the real MobileNetV2 model, then captured in headless Chrome",
        "browser": chrome_version,
        "server_log": "report_evidence/ch5-8/screenshot_server.log",
        "screenshots": captured,
    }
    with open(os.path.join(OUTPUT_DIR, "screenshots.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    print(f"captured {len(captured)} screenshots")


if __name__ == "__main__":
    main()
