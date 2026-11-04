"""Smoke-test the authenticated /predict JSON endpoint.

Start the application first, then run:
    python test_api.py

Credentials default to the seeded Administrator and can be overridden with
the MAIZE_USERNAME and MAIZE_PASSWORD environment variables.
"""

import json
import os
import re

import requests

BASE_URL = os.environ.get("MAIZE_BASE_URL", "http://127.0.0.1:5000")
USERNAME = os.environ.get("MAIZE_USERNAME", "rexious_admin")
PASSWORD = os.environ.get("MAIZE_PASSWORD", "AdminPass123!")
TEST_DIR = os.environ.get("MAIZE_TEST_DIR", os.path.join("data", "processed", "test", "MSV"))
SECTION_ID = os.environ.get("MAIZE_SECTION_ID", "1")


def extract_csrf(html):
    match = re.search(r'name="_csrf_token"\s+value="([^"]+)"', html)
    if not match:
        match = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    return match.group(1) if match else None


def main():
    if not os.path.isdir(TEST_DIR):
        print(f"[!] Test image directory not found: {TEST_DIR}")
        return 1

    images = [
        name for name in os.listdir(TEST_DIR)
        if name.lower().endswith((".png", ".jpg", ".jpeg"))
    ]
    if not images:
        print(f"[!] No image files found in '{TEST_DIR}'")
        return 1

    sample_image_path = os.path.join(TEST_DIR, images[0])
    print(f"[+] Testing API using: {sample_image_path}")

    session = requests.Session()
    login_page = session.get(f"{BASE_URL}/login", timeout=15)
    token = extract_csrf(login_page.text)
    if not token:
        print("[!] Could not obtain a CSRF token from the login page.")
        return 1

    login_response = session.post(
        f"{BASE_URL}/login",
        data={"login_input": USERNAME, "password": PASSWORD, "_csrf_token": token},
        timeout=15,
    )
    if "/login" in login_response.url:
        print("[!] Sign-in failed. Check MAIZE_USERNAME and MAIZE_PASSWORD.")
        return 1

    dashboard = session.get(f"{BASE_URL}/farmer/dashboard", timeout=15)
    token = extract_csrf(dashboard.text)
    if not token:
        print("[!] Could not obtain a CSRF token from the dashboard.")
        return 1

    with open(sample_image_path, "rb") as image_file:
        response = session.post(
            f"{BASE_URL}/predict",
            files={"image": (os.path.basename(sample_image_path), image_file, "image/jpeg")},
            data={"section_id": SECTION_ID, "_csrf_token": token},
            timeout=120,
        )

    print("\n--- API RESPONSE ---")
    try:
        print(json.dumps(response.json(), indent=4))
    except ValueError:
        print(response.text)
    return 0 if response.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
