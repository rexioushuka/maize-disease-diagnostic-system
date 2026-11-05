"""Centralised input validation and password policy (SEC-14, SEC-17)."""

import re

# Minimum 12 characters with at least three character classes. Reject common
# weak patterns. This is applied everywhere a password is set.
_MIN_LEN = 12
_WEAK = {
    "password", "password123", "adminpass123", "123456789012", "qwertyuiop",
    "mubas-1234", "letmein12345",
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-()]{6,18}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_NAME_RE = re.compile(r"^[^\x00-\x1f<>]{2,120}$")


def password_errors(password):
    errors = []
    if not password or len(password) < _MIN_LEN:
        errors.append("min_length")
    if not re.search(r"[A-Z]", password or ""):
        errors.append("uppercase")
    if not re.search(r"[a-z]", password or ""):
        errors.append("lowercase")
    if not re.search(r"[0-9]", password or ""):
        errors.append("digit")
    if not re.search(r"[^A-Za-z0-9]", password or ""):
        errors.append("symbol")
    if (password or "").strip().lower() in _WEAK:
        errors.append("common")
    return errors


def valid_username(value):
    return bool(value and _USERNAME_RE.match(value))


def valid_full_name(value):
    return bool(value and _NAME_RE.match(value))


def parse_contact(contact):
    """Return ('email', value) or ('phone', normalised) or (None, None)."""
    if not contact:
        return None, None
    value = contact.strip()
    if "@" in value:
        if _EMAIL_RE.match(value):
            return "email", value.lower()
        return None, None
    if _PHONE_RE.match(value):
        normalised = re.sub(r"[\s\-()]", "", value)
        return "phone", normalised
    return None, None


def bounded(value, max_len=255):
    return (value or "").strip()[:max_len]
