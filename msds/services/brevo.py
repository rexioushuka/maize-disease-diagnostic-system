"""Brevo (Sendinblue) transactional email/SMS delivery.

Credentials come from the environment or ``instance/brevo.env`` (never from
source code).  Email can go through the REST API (``BREVO_API_KEY``) or, when
no API key is set, through the SMTP relay (``BREVO_SMTP_*``).  SMS is only
available through the REST API.  Used for registration verification codes and,
via :func:`msds.services.notifications.deliver_secret`, invitation codes and
temporary passwords.
"""

import json
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

from flask import current_app

BREVO_API = "https://api.brevo.com/v3"
_TIMEOUT = 10


class BrevoError(RuntimeError):
    """Raised when Brevo cannot deliver a message."""


def brevo_configured():
    return bool(current_app.config.get("BREVO_API_KEY")
                or (current_app.config.get("BREVO_SMTP_LOGIN")
                    and current_app.config.get("BREVO_SMTP_KEY")))


def smtp_configured():
    return bool(current_app.config.get("BREVO_SMTP_LOGIN")
                and current_app.config.get("BREVO_SMTP_KEY"))


def _api_key():
    key = current_app.config.get("BREVO_API_KEY")
    if not key:
        raise BrevoError("BREVO_API_KEY is not configured")
    return key


def _post(path, payload):
    request = urllib.request.Request(
        BREVO_API + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-key": _api_key(),
            "content-type": "application/json",
            "accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")[:300]
        raise BrevoError(f"Brevo HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise BrevoError(f"Brevo unreachable: {exc.reason}") from exc


def _send_email_smtp(to_email, subject, text_content):
    sender_email = current_app.config.get("BREVO_SENDER_EMAIL")
    if not sender_email:
        raise BrevoError("BREVO_SENDER_EMAIL is not configured")
    sender_name = current_app.config.get("BREVO_SENDER_NAME") or "Maize Disease System"
    message = EmailMessage()
    message["From"] = f"{sender_name} <{sender_email}>"
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text_content)
    host = current_app.config.get("BREVO_SMTP_HOST") or "smtp-relay.brevo.com"
    port = int(current_app.config.get("BREVO_SMTP_PORT") or 587)
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(current_app.config["BREVO_SMTP_LOGIN"],
                       current_app.config["BREVO_SMTP_KEY"])
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise BrevoError(f"SMTP delivery failed: {exc}") from exc


def send_email(to_email, subject, text_content):
    if current_app.config.get("BREVO_API_KEY"):
        sender_email = current_app.config.get("BREVO_SENDER_EMAIL")
        if not sender_email:
            raise BrevoError("BREVO_SENDER_EMAIL is not configured")
        payload = {
            "sender": {
                "email": sender_email,
                "name": current_app.config.get("BREVO_SENDER_NAME")
                or "Maize Disease System",
            },
            "to": [{"email": to_email}],
            "subject": subject,
            "textContent": text_content,
        }
        return _post("/smtp/email", payload)
    if smtp_configured():
        return _send_email_smtp(to_email, subject, text_content)
    raise BrevoError("Brevo email is not configured (set BREVO_API_KEY or BREVO_SMTP_*)")


def send_sms(to_phone, content):
    if not current_app.config.get("BREVO_API_KEY"):
        raise BrevoError(
            "SMS delivery requires BREVO_API_KEY; the SMTP relay is email-only."
        )
    payload = {
        "sender": current_app.config.get("BREVO_SMS_SENDER") or "MaizeSys",
        "recipient": to_phone.lstrip("+"),
        "content": content,
        "type": "transactional",
    }
    return _post("/transactionalSMS/sms", payload)


def send_verification_code(contact_kind, contact, code):
    if contact_kind == "email":
        send_email(
            contact,
            "Your Maize Disease System verification code",
            (f"Your Maize Crop Disease System verification code is: {code}\n\n"
             "It expires in 10 minutes. If you did not request this code, "
             "ignore this message."),
        )
    elif contact_kind == "phone":
        send_sms(contact, f"Maize Disease System code: {code}. Valid 10 min.")
    else:
        raise BrevoError(f"Unknown contact kind: {contact_kind}")
