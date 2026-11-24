"""Evaluate a trained maize disease model and export evidence artifacts.

Outputs:
- classification report (accuracy, precision, recall, F1-score)
- confusion matrix plot
- JSON metrics report
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import tensorflow as tf
from tensorflow.keras.preprocessing.image import ImageDataGenerator

try:
    from sklearn.metrics import classification_report, confusion_matrix
    SKLEARN_AVAILABLE = True
except Exception:
    SKLEARN_AVAILABLE = False

    def confusion_matrix(y_true, y_pred, labels=None):
        labels = list(range(int(max(max(y_true), max(y_pred)) + 1)) if labels is None else labels)
        idx = {l: i for i, l in enumerate(labels)}
        cm = np.zeros((len(labels), len(labels)), dtype=int)
        for t, p in zip(y_true, y_pred):
            if t in idx and p in idx:
                cm[idx[t], idx[p]] += 1
        return cm

    def classification_report(y_true, y_pred, target_names=None, digits=4, output_dict=False, zero_division=0):
        labels = sorted(set(y_true) | set(y_pred))
        names = target_names or [str(l) for l in labels]
        per = {}
        total = len(y_true)
        for i, label in enumerate(labels):
            tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
            fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
            fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
            support = sum(1 for t in y_true if t == label)
            prec = tp / (tp + fp) if (tp + fp) else float(zero_division)
            rec = tp / (tp + fn) if (tp + fn) else float(zero_division)
            f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else float(zero_division)
            per[names[i]] = {"precision": prec, "recall": rec, "f1-score": f1, "support": support}
        accuracy = sum(1 for t, p in zip(y_true, y_pred) if t == p) / total if total else 0.0
        macro = {k: sum(m[k] for m in per.values()) / len(per) if per else 0.0 for k in ["precision", "recall", "f1-score"]}
        macro["support"] = total
        weighted = {k: sum(m[k] * m["support"] for m in per.values()) / total if total else 0.0 for k in ["precision", "recall", "f1-score"]}
        weighted["support"] = total
        report = {"accuracy": accuracy, "macro avg": macro, "weighted avg": weighted, **per}
        if output_dict:
            return report
        lines = ["".ljust(20) + "precision recall f1-score support"]
        for n in names:
            m = per[n]
            lines.append(f"{n:20s}{m['precision']:{digits+6}.{digits}f}{m['recall']:{digits+2}.{digits}f}{m['f1-score']:{digits+2}.{digits}f}{m['support']:9d}")
        lines.append(f"{'accuracy':20s}{'':>{digits+6}}{'':>{digits+2}}{accuracy:{digits+2}.{digits}f}{total:9d}")
        for k, lbl in [("macro avg", "macro avg"), ("weighted avg", "weighted avg")]:
            m = report[k]
            lines.append(f"{lbl:20s}{m['precision']:{digits+6}.{digits}f}{m['recall']:{digits+2}.{digits}f}{m['f1-score']:{digits+2}.{digits}f}{m['support']:9d}")
        return "\n".join(lines)

try:
    import matplotlib.pyplot as plt
    import seaborn as sns
    PLOTTING_AVAILABLE = True
except Exception as exc:  # broken/incomplete install in some envs
    plt = None
    sns = None
    PLOTTING_AVAILABLE = False
    PLOT_IMPORT_ERROR = exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate maize disease classifier")
    parser.add_argument("--model", default="models/mobilenetv2_maize.keras", help="Path to .keras model")
    parser.add_argument("--mapping", default="models/class_mapping.json", help="Path to class mapping JSON")
    parser.add_argument("--test-dir", default="data/processed/test", help="Path to test split directory")
    parser.add_argument("--report", default="models/evaluation_report_v2.json", help="Path to JSON metrics report")
    parser.add_argument("--cm-plot", default="models/confusion_matrix_v2.png", help="Path to confusion matrix image")
    parser.add_argument("--img-size", type=int, nargs=2, default=[224, 224], metavar=("H", "W"))
    parser.add_argument("--batch-size", type=int, default=32)
    return parser.parse_args()


def load_expected_classes(mapping_path: Path) -> list[str]:
    with mapping_path.open("r", encoding="utf-8") as fh:
        mapping = json.load(fh)
    class_names = mapping.get("class_names")
    if not isinstance(class_names, list) or not class_names:
        raise ValueError(f"Invalid or missing class_names in {mapping_path}")
    return class_names, mapping


def main() -> None:
    args = parse_args()
    model_path = Path(args.model)
    mapping_path = Path(args.mapping)
    test_dir = Path(args.test_dir)
    report_path = Path(args.report)
    cm_plot_path = Path(args.cm_plot)
    img_size = tuple(args.img_size)

    for p, label in [(model_path, "model"), (mapping_path, "mapping"), (test_dir, "test directory")]:
        if not p.exists():
            raise FileNotFoundError(f"Missing {label}: {p}")

    expected_classes, mapping = load_expected_classes(mapping_path)

    model = tf.keras.models.load_model(model_path, safe_mode=True, compile=False)
    gen = ImageDataGenerator(rescale=1.0 / 255.0).flow_from_directory(
        str(test_dir),
        target_size=img_size,
        batch_size=args.batch_size,
        class_mode="categorical",
        shuffle=False,
    )

    class_labels = sorted(gen.class_indices, key=gen.class_indices.get)
    if class_labels != expected_classes:
        raise RuntimeError(
            f"Test split class order {class_labels} does not match mapping {expected_classes}"
        )

    predictions = model.predict(gen, verbose=1)
    y_pred = np.argmax(predictions, axis=1)
    y_true = gen.classes

    report_dict = classification_report(
        y_true,
        y_pred,
        target_names=class_labels,
        digits=4,
        output_dict=True,
        zero_division=0,
    )
    report_text = classification_report(
        y_true,
        y_pred,
        target_names=class_labels,
        digits=4,
        zero_division=0,
    )

    cm = confusion_matrix(y_true, y_pred, labels=list(range(len(class_labels))))

    print("\n== MODEL EVALUATION ==")
    print(f"Model: {model_path}")
    print(f"Test samples: {len(y_true)}")
    print(f"Accuracy: {report_dict['accuracy']:.4f}")
    print(f"Macro Precision: {report_dict['macro avg']['precision']:.4f}")
    print(f"Macro Recall: {report_dict['macro avg']['recall']:.4f}")
    print(f"Macro F1-score: {report_dict['macro avg']['f1-score']:.4f}")
    print(f"Weighted F1-score: {report_dict['weighted avg']['f1-score']:.4f}\n")
    print(report_text)

    cm_plot_path.parent.mkdir(parents=True, exist_ok=True)
    if PLOTTING_AVAILABLE:
        plt.figure(figsize=(9, 7))
        sns.heatmap(
            cm,
            annot=True,
            fmt="d",
            cmap="Blues",
            xticklabels=class_labels,
            yticklabels=class_labels,
        )
        plt.title("Confusion Matrix - Maize Disease Classification")
        plt.ylabel("True Label")
        plt.xlabel("Predicted Label")
        plt.tight_layout()
        plt.savefig(cm_plot_path, dpi=300, bbox_inches="tight")
        plt.close()
    else:
        print(f"WARNING: plotting unavailable ({PLOT_IMPORT_ERROR}); skipping confusion matrix image.")

    payload = {
        "model_path": str(model_path),
        "model_version": mapping.get("model_version"),
        "model_sha256": mapping.get("sha256"),
        "test_dir": str(test_dir),
        "n_test_samples": int(len(y_true)),
        "class_names": class_labels,
        "accuracy": float(report_dict["accuracy"]),
        "macro_avg": {
            "precision": float(report_dict["macro avg"]["precision"]),
            "recall": float(report_dict["macro avg"]["recall"]),
            "f1_score": float(report_dict["macro avg"]["f1-score"]),
            "support": int(report_dict["macro avg"]["support"]),
        },
        "weighted_avg": {
            "precision": float(report_dict["weighted avg"]["precision"]),
            "recall": float(report_dict["weighted avg"]["recall"]),
            "f1_score": float(report_dict["weighted avg"]["f1-score"]),
            "support": int(report_dict["weighted avg"]["support"]),
        },
        "per_class": {
            label: {
                "precision": float(report_dict[label]["precision"]),
                "recall": float(report_dict[label]["recall"]),
                "f1_score": float(report_dict[label]["f1-score"]),
                "support": int(report_dict[label]["support"]),
            }
            for label in class_labels
        },
        "confusion_matrix": cm.tolist(),
        "confusion_matrix_plot": str(cm_plot_path),
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    if PLOTTING_AVAILABLE:
        print(f"Confusion matrix saved to: {cm_plot_path}")
    else:
        print("Confusion matrix image not saved (matplotlib/seaborn unavailable).")
    print(f"Metrics report saved to: {report_path}")


if __name__ == "__main__":
    main()
