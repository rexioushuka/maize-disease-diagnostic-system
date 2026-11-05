"""Evaluate the classifier on the untouched test split and persist metrics.

Fixes SEC-23:
* Asserts that the test-split class order matches the authoritative mapping.
* Persists a JSON metrics report keyed by model version (auditable).
* Uses the safe ``.keras`` format.
"""

import json
import os

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
import tensorflow as tf
from sklearn.metrics import classification_report, confusion_matrix
from tensorflow.keras.preprocessing.image import ImageDataGenerator

PROCESSED_DIR = "data/processed"
MODEL_PATH = "models/mobilenetv2_maize.keras"
MAPPING_PATH = "models/class_mapping.json"
CM_PLOT_PATH = "models/confusion_matrix.png"
REPORT_PATH = "models/evaluation_report.json"
IMG_SIZE = (224, 224)
BATCH_SIZE = 32


def evaluate_model():
    print("\n== PHASE 3: MODEL EVALUATION ==\n")
    with open(MAPPING_PATH, "r", encoding="utf-8") as fh:
        mapping = json.load(fh)
    expected = mapping["class_names"]

    model = tf.keras.models.load_model(MODEL_PATH, safe_mode=True, compile=False)
    gen = ImageDataGenerator(rescale=1.0 / 255.0).flow_from_directory(
        os.path.join(PROCESSED_DIR, "test"),
        target_size=IMG_SIZE, batch_size=BATCH_SIZE,
        class_mode="categorical", shuffle=False,
    )
    class_labels = sorted(gen.class_indices, key=gen.class_indices.get)
    if class_labels != expected:
        raise RuntimeError(
            f"test split class order {class_labels} != mapping {expected}")

    predictions = model.predict(gen, verbose=1)
    y_pred = np.argmax(predictions, axis=1)
    y_true = gen.classes

    report = classification_report(
        y_true, y_pred, target_names=class_labels, digits=4, output_dict=True)
    print(classification_report(y_true, y_pred, target_names=class_labels, digits=4))

    cm = confusion_matrix(y_true, y_pred)
    plt.figure(figsize=(8, 6))
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
                xticklabels=class_labels, yticklabels=class_labels)
    plt.title("Confusion Matrix - Maize Disease Diagnostic Model", fontsize=12)
    plt.ylabel("True Class")
    plt.xlabel("Predicted Class")
    plt.tight_layout()
    plt.savefig(CM_PLOT_PATH, dpi=300)
    plt.close()

    payload = {
        "model_version": mapping.get("model_version"),
        "model_sha256": mapping.get("sha256"),
        "class_names": class_labels,
        "n_test": int(len(y_true)),
        "accuracy": float(report["accuracy"]),
        "macro_f1": float(report["macro avg"]["f1-score"]),
        "per_class": {k: v for k, v in report.items()
                      if k in class_labels},
        "confusion_matrix": cm.tolist(),
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"[✓] Confusion matrix -> {CM_PLOT_PATH}")
    print(f"[✓] Metrics report -> {REPORT_PATH}")


if __name__ == "__main__":
    evaluate_model()
