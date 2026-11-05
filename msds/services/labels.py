"""Display-label helpers shared by templates and blueprints (QUA-05)."""

from flask import session

from ..i18n import translate

ROLE_LABEL_KEYS = {
    "FARMER": "status.role_farmer",
    "EXTENSION_OFFICER": "status.role_officer",
    "ADMIN": "status.role_admin",
}

CLASS_LABEL_CODES = {
    "Downy_Mildew": "Downy_Mildew",
    "Downy Mildew": "Downy_Mildew",
    "Healthy": "Healthy",
    "MLN": "MLN",
    "Maize Lethal Necrosis (MLN)": "MLN",
    "MSV": "MSV",
    "Maize Streak Virus (MSV)": "MSV",
}


def _lang():
    return session.get("lang", "en")


def role_label(value):
    key = ROLE_LABEL_KEYS.get(str(value or "").upper())
    return translate(_lang(), key) if key else str(value or "").replace("_", " ").title()


def status_label(value):
    if not value:
        return ""
    key = f"status.{str(value).lower()}"
    label = translate(_lang(), key)
    return str(value).replace("_", " ").title() if label == key else label


def class_label(value):
    code = CLASS_LABEL_CODES.get(str(value or ""), str(value or ""))
    key = f"class.{code}"
    label = translate(_lang(), key)
    return str(value) if label == key else label
