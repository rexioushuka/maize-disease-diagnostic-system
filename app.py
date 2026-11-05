"""Backwards-compatible entrypoint.

The application now lives in the :mod:`msds` package.  ``python app.py`` still
starts a development server; production uses ``gunicorn wsgi:app``.
"""

import os

from msds import create_app

app = create_app(os.environ.get("APP_CONFIG", "development"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "0.0.0.0")
    if host not in ("127.0.0.1", "localhost"):
        print("[!] Serving plain HTTP. Browsers block the live camera on "
              "non-localhost HTTP (secure-context rule).")
        print("    For LAN access with camera enabled, stop this and run:")
        print("      python run_https.py")
        print(f"    then open https://<this-host>:{os.environ.get('HTTPS_PORT', '5443')}")
    app.run(host=host, port=port,
            debug=os.environ.get("FLASK_DEBUG", "0") == "1")
