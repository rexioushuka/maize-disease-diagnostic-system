"""Smoke-test the authenticated JSON endpoint against a running server.

Credentials are read from the environment only; there are no hard-coded
defaults (SEC-01).

    set MAIZE_USERNAME=...
    set MAIZE_PASSWORD=...
    python test_api.py
"""

import json
import os
import re

import requests

BASE_URL = os.environ.get("MAIZE_BASE_URL", "http://127.0.0.1:5000")
USERNAME = os.environ.get("MAIZE_USERNAME")
PASSWORD = os.environ.get("MAIZE_PASSWORD")
TEST_DIR = os.environ.get("MAIZE_TEST_DIR", os.path.join("data", "processed", "test", "MSV"))
SECTION_ID = os.environ.get("MAIZE_SECTION_ID", "1")


def extract_csrf(html):
    match = re.search(r'name="_csrf_token"\s+value="([^"]+)"', html)
    if not match:
        match = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    return match.group(1) if match else None


def main():
    if not USERNAME or not PASSWORD:
        print("[!] Set MAIZE_USERNAME and MAIZE_PASSWORD environment variables.")
        return 1
    if not os.path.isdir(TEST_DIR):
        print(f"[!] Test image directory not found: {TEST_DIR}")
        return 1
    images = [n for n in os.listdir(TEST_DIR)
              if n.lower().endswith((".png", ".jpg", ".jpeg"))]
    if not images:
        print(f"[!] No image files found in '{TEST_DIR}'")
        return 1
    sample = os.path.join(TEST_DIR, images[0])

    session = requests.Session()
    token = extract_csrf(session.get(f"{BASE_URL}/login", timeout=15).text)
    if not token:
        print("[!] Could not obtain a CSRF token from the login page.")
        return 1

    resp = session.post(
        f"{BASE_URL}/login",
        data={"login_input": USERNAME, "password": PASSWORD, "_csrf_token": token},
        timeout=15,
    )
    if "/login" in resp.url:
        print("[!] Sign-in failed. Check MAIZE_USERNAME / MAIZE_PASSWORD.")
        return 1

    token = extract_csrf(session.get(f"{BASE_URL}/farmer/dashboard", timeout=15).text)
    if not token:
        print("[!] Could not obtain a CSRF token from the dashboard.")
        return 1

    with open(sample, "rb") as image_file:
        response = session.post(
            f"{BASE_URL}/api/v1/predict",
            files={"image": (os.path.basename(sample), image_file, "image/jpeg")},
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
