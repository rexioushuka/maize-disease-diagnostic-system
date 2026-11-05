"""Out-of-band delivery for codes/tokens (SEC-15).

Brevo is used when BREVO_API_KEY is configured (see msds/services/brevo.py).
Otherwise MAIL_DSN / SMS_GATEWAY_DSN are consulted.  If neither works the
caller decides how to surface it; in this build we refuse to echo secrets in
the UI and log a warning instead.
"""

from flask import current_app

from . import brevo


def _deliver(channel, contact, subject, body):
    dsn = current_app.config.get("MAIL_DSN" if channel == "email" else "SMS_GATEWAY_DSN")
    if not dsn:
        current_app.logger.warning(
            "No %s gateway configured; secret for %s was not delivered. "
            "Configure MAIL_DSN/SMS_GATEWAY_DSN or deliver it manually.",
            channel, contact,
        )
        return False
    # A real implementation would use the DSN (e.g. smtplib / HTTP gateway).
    current_app.logger.info("[%s] %s -> %s", channel, subject, contact)
    return True


def deliver_secret(contact, subject, body):
    channel = "email" if contact and "@" in contact else "sms"
    if brevo.brevo_configured():
        try:
            if channel == "email":
                brevo.send_email(contact, subject, body)
            else:
                brevo.send_sms(contact, body)
            current_app.logger.info("[brevo] Delivered '%s' to %s", subject, contact)
            return True
        except brevo.BrevoError as exc:
            current_app.logger.error("Brevo delivery to %s failed: %s",
                                     contact, exc)
            return False
    return _deliver(channel, contact, subject, body)
