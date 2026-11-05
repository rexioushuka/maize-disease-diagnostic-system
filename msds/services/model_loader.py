"""Model load/build helpers (lazy and testable)."""

import logging

from flask import current_app

from .model_registry import load_mapping, verify_model_hash

log = logging.getLogger(__name__)


def build_model():
    """Load the model according to config, or a deterministic stub in testing."""
    if not current_app.config.get("LOAD_MODEL", True):
        return _StubModel(current_app.config["CLASS_NAMES"])

    import tensorflow as tf  # imported lazily to keep tests light

    path = current_app.config["MODEL_PATH"]
    if current_app.config.get("MODEL_HASH_VERIFY", True):
        mapping = load_mapping(current_app.config["CLASS_MAPPING_PATH"])
        verify_model_hash(path, mapping.get("sha256"))

    model = tf.keras.models.load_model(path, safe_mode=True, compile=False)
    expected = len(current_app.config["CLASS_NAMES"])
    if int(model.output_shape[-1]) != expected:
        raise RuntimeError(
            f"model output dimension {model.output_shape[-1]} != {expected} classes"
        )
    return model


class _StubModel:
    """Hands back a fixed softmax vector; used when LOAD_MODEL=0."""

    def __init__(self, class_names):
        import numpy as np
        n = len(class_names)
        self._probs = np.full(n, 0.1 / max(n - 1, 1), dtype="float32")
        self._probs[0] = 0.9

    def predict(self, batch, verbose=0):
        return [self._probs.copy()]
