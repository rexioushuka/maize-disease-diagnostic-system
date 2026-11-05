"""Run the maize disease system over HTTPS with a local certificate authority.

The local CA signs a server certificate covering the current LAN IP, hostname
and localhost.  Install ``maize-ca-cert.crt`` in each client's trust store once.

Usage:
    python run_https.py
    python run_https.py --force-cert

Security notes:
* The CA private key is written with restricted permissions and must never be
  shared (it can issue certificates for any host the trust store honours).
* For an internet-facing deployment terminate TLS at a real reverse proxy
  (Caddy/nginx) instead of this development launcher.
"""

import os
import socket
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CERT_DIR = BASE_DIR / "instance" / "certs"
CA_CERT = CERT_DIR / "maize-ca-cert.pem"
CA_KEY = CERT_DIR / "maize-ca-key.pem"
CA_CRT = CERT_DIR / "maize-ca-cert.crt"
LEAF_FILE = CERT_DIR / "maize-lan-leaf.pem"
CERT_FILE = CERT_DIR / "maize-lan-cert.pem"
KEY_FILE = CERT_DIR / "maize-lan-key.pem"
CSR_FILE = CERT_DIR / "maize-lan.csr"
EXT_FILE = CERT_DIR / "maize-lan-ext.cnf"
SERIAL_FILE = CERT_DIR / "maize-lan.srl"

import shutil

OPENSSL_CANDIDATES = [
    shutil.which("openssl"),
    r"C:\Program Files\Git\usr\bin\openssl.exe",
    r"C:\Program Files\Git\mingw64\bin\openssl.exe",
    r"C:\Program Files\OpenSSL-Win64\bin\openssl.exe",
    r"C:\Program Files\OpenSSL\bin\openssl.exe",
]


def find_openssl():
    for candidate in OPENSSL_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def lan_ips():
    ips = {"127.0.0.1"}
    primary = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            primary = sock.getsockname()[0]
            ips.add(primary)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    for extra in os.environ.get("SSL_EXTRA_IPS", "").split(","):
        extra = extra.strip()
        if extra:
            ips.add(extra)
    ordered = [primary] if primary else []
    ordered.extend(sorted(
        ip for ip in ips if ip != primary and not ip.startswith("169.254.")))
    return ordered


def run_openssl(openssl, arguments):
    result = subprocess.run([openssl, *arguments], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr or result.stdout, file=sys.stderr)
        raise RuntimeError("OpenSSL command failed.")
    return result


def ensure_ca(openssl):
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    if CA_CERT.exists() != CA_KEY.exists():
        raise RuntimeError(
            "The local CA is incomplete. Delete instance/certs and run the launcher again."
        )
    if not CA_CERT.exists():
        run_openssl(openssl, [
            "req", "-x509", "-newkey", "rsa:4096", "-sha256", "-nodes",
            "-keyout", str(CA_KEY), "-out", str(CA_CERT), "-days", "3650",
            "-subj", "/CN=Maize Disease System Local CA/O=Maize Disease System",
            "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        ])
    CA_CRT.write_bytes(CA_CERT.read_bytes())
    for private_file in (CA_KEY,):
        try:
            os.chmod(private_file, 0o600)
        except OSError:
            pass
    return CA_CERT, CA_KEY


def cert_is_valid(openssl, ips):
    if not all(path.is_file() for path in (CA_CERT, LEAF_FILE, CERT_FILE, KEY_FILE)):
        return False
    verify = subprocess.run(
        [openssl, "verify", "-CAfile", str(CA_CERT), str(LEAF_FILE)],
        capture_output=True, text=True)
    if verify.returncode != 0:
        return False
    expiry = subprocess.run(
        [openssl, "x509", "-in", str(LEAF_FILE), "-noout", "-checkend", "86400"],
        capture_output=True, text=True)
    if expiry.returncode != 0:
        return False
    details = subprocess.run(
        [openssl, "x509", "-in", str(LEAF_FILE), "-noout", "-text"],
        capture_output=True, text=True)
    if details.returncode != 0:
        return False
    return all(f"IP Address:{ip}" in details.stdout for ip in ips)


def create_leaf(openssl, ips):
    alt_names = ["DNS:localhost"]
    hostname = socket.gethostname().strip()
    if hostname and all(ch.isalnum() or ch in ".-" for ch in hostname):
        alt_names.append(f"DNS:{hostname}")
    alt_names.extend(f"IP:{ip}" for ip in ips)

    run_openssl(openssl, [
        "req", "-new", "-newkey", "rsa:2048", "-sha256", "-nodes",
        "-keyout", str(KEY_FILE), "-out", str(CSR_FILE),
        "-subj", "/CN=Maize Crop Disease Diagnostic System",
    ])
    EXT_FILE.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        f"subjectAltName={','.join(alt_names)}\n",
        encoding="utf-8",
    )
    run_openssl(openssl, [
        "x509", "-req", "-in", str(CSR_FILE), "-CA", str(CA_CERT),
        "-CAkey", str(CA_KEY), "-CAserial", str(SERIAL_FILE),
        "-CAcreateserial", "-out", str(LEAF_FILE), "-days", "825",
        "-sha256", "-extfile", str(EXT_FILE),
    ])
    CERT_FILE.write_bytes(LEAF_FILE.read_bytes() + CA_CERT.read_bytes())
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass


def ensure_certificate(force=False):
    openssl = find_openssl()
    if not openssl:
        raise RuntimeError(
            "OpenSSL was not found. Install Git for Windows or OpenSSL and retry.")
    ips = lan_ips()
    ensure_ca(openssl)
    if force or not cert_is_valid(openssl, ips):
        create_leaf(openssl, ips)
    return CERT_FILE, KEY_FILE, ips, CA_CRT


def start_http_redirect(http_port, https_port):
    """Serve a tiny HTTP->HTTPS (308) redirector on *http_port* in the background."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _RedirectHandler(BaseHTTPRequestHandler):
        def _redirect(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            if not host:
                host = "127.0.0.1"
            location = f"https://{host}:{https_port}{self.path}"
            self.send_response(308)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = _redirect
        do_HEAD = _redirect
        do_POST = _redirect
        do_PUT = _redirect
        do_PATCH = _redirect
        do_DELETE = _redirect

        def log_message(self, *args):
            pass

    try:
        server = ThreadingHTTPServer(("0.0.0.0", http_port), _RedirectHandler)
    except OSError as exc:
        print(f"[!] Port {http_port} unavailable for HTTP->HTTPS redirect ({exc}). "
              f"HTTPS on port {https_port} still works.")
        return
    thread = threading.Thread(target=server.serve_forever,
                              name="http-redirect", daemon=True)
    thread.start()
    print(f"HTTP redirect: http://<host>:{http_port} -> https://<host>:{https_port}")


def main():
    cert_file, key_file, ips, ca_file = ensure_certificate(
        force="--force-cert" in sys.argv)

    http_port = int(os.environ.get("PORT", "5000"))
    https_port = int(os.environ.get("HTTPS_PORT", "5443"))
    os.environ["SSL_CERT"] = str(cert_file)
    os.environ["SSL_KEY"] = str(key_file)
    os.environ["SESSION_COOKIE_SECURE"] = "1"
    os.environ.setdefault("FLASK_DEBUG", "0")

    lan_ip = next((ip for ip in ips if ip != "127.0.0.1"), "127.0.0.1")
    print(f"Local CA certificate: {ca_file}")
    print("Install that CA certificate in each client's trusted root store once.")
    print(f"Open on this network: https://{lan_ip}:{https_port}")

    start_http_redirect(http_port, https_port)

    import ssl

    from msds import create_app

    app = create_app("development")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    context.load_cert_chain(str(cert_file), str(key_file))
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["PREFERRED_URL_SCHEME"] = "https"
    app.run(host="0.0.0.0", port=https_port, ssl_context=context,
            use_reloader=False, debug=False)


if __name__ == "__main__":
    main()
