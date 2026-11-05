"""Authoritative class mapping loaded next to the model (SEC-21)."""

import hashlib
import json
import os


class ModelIntegrityError(RuntimeError):
    pass


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_mapping(mapping_path):
    with open(mapping_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    classes = list(data["class_names"])
    if len(set(classes)) != len(classes):
        raise ModelIntegrityError("class_names contains duplicates")
    return {
        "model_version": data.get("model_version", "unknown"),
        "input_size": tuple(data.get("input_size", (224, 224))),
        "class_names": classes,
        "sha256": data.get("sha256", ""),
        "file": data.get("file", ""),
    }


def verify_model_hash(model_path, expected_sha256):
    if not expected_sha256:
        return True
    if not os.path.exists(model_path):
        raise ModelIntegrityError(f"model file missing: {model_path}")
    actual = _sha256(model_path)
    if actual.lower() != expected_sha256.lower():
        raise ModelIntegrityError(
            f"model hash mismatch: expected {expected_sha256}, got {actual}"
        )
    return True
