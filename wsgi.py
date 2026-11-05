"""WSGI entrypoint.  ``gunicorn wsgi:app``."""

import os

from msds import create_app

app = create_app(os.environ.get("APP_CONFIG", "production" if os.environ.get("SERVICE_MODE") else "development"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
