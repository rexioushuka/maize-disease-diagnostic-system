"""Train the MobileNetV2 classifier and save a safe ``.keras`` model.

Fixes QUA-04 / SEC-21 / SEC-03:
* Saves to the modern ``.keras`` format (safe, no Pickle/Lambda deserialisation).
* Writes metrics and the authoritative class mapping next to the model.
* Uses deterministic, leak-aware splits produced by ``prep_dataset.py``.
"""

import json
import os

import matplotlib.pyplot as plt
import tensorflow as tf
from tensorflow.keras.applications import MobileNetV2
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint
from tensorflow.keras.layers import Dense, Dropout, GlobalAveragePooling2D
from tensorflow.keras.models import Model
from tensorflow.keras.preprocessing.image import ImageDataGenerator

PROCESSED_DIR = "data/processed"
MODEL_SAVE_PATH = "models/mobilenetv2_maize.keras"
PLOT_SAVE_PATH = "models/training_history.png"
METRICS_PATH = "models/training_metrics.json"
CLASS_ORDER = ["Downy_Mildew", "Healthy", "MLN", "MSV"]

IMG_SIZE = (224, 224)
BATCH_SIZE = 32
EPOCHS = 15
SEED = 42


def _assert_class_order(generator):
    detected = generator.class_indices
    ordered = sorted(detected, key=detected.get)
    if ordered != CLASS_ORDER:
        raise RuntimeError(
            f"class order mismatch: folders={ordered} expected={CLASS_ORDER}. "
            "Rename folders or update CLASS_ORDER explicitly."
        )


def train_model():
    print("\n== PHASE 2: MODEL TRAINING (CNN) ==\n")

    train_datagen = ImageDataGenerator(
        rescale=1.0 / 255.0,
        rotation_range=20,
        width_shift_range=0.1,
        height_shift_range=0.1,
        zoom_range=0.15,
        horizontal_flip=True,
        fill_mode="nearest",
    )
    val_datagen = ImageDataGenerator(rescale=1.0 / 255.0)

    train_generator = train_datagen.flow_from_directory(
        os.path.join(PROCESSED_DIR, "train"),
        target_size=IMG_SIZE, batch_size=BATCH_SIZE,
        class_mode="categorical", shuffle=True, seed=SEED,
    )
    val_generator = val_datagen.flow_from_directory(
        os.path.join(PROCESSED_DIR, "val"),
        target_size=IMG_SIZE, batch_size=BATCH_SIZE,
        class_mode="categorical", shuffle=False,
    )
    _assert_class_order(train_generator)

    base_model = MobileNetV2(weights="imagenet", include_top=False,
                             input_shape=(224, 224, 3))
    base_model.trainable = False
    x = base_model.output
    x = GlobalAveragePooling2D()(x)
    x = Dropout(0.3)(x)
    x = Dense(128, activation="relu")(x)
    predictions = Dense(len(CLASS_ORDER), activation="softmax")(x)
    model = Model(inputs=base_model.input, outputs=predictions)

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.0001),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )

    callbacks = [
        ModelCheckpoint(MODEL_SAVE_PATH, monitor="val_accuracy",
                        save_best_only=True, verbose=1),
        EarlyStopping(monitor="val_loss", patience=4,
                      restore_best_weights=True, verbose=1),
    ]
    history = model.fit(train_generator, epochs=EPOCHS,
                        validation_data=val_generator, callbacks=callbacks)

    plot_history(history)
    _write_metrics(history)
    print(f"\n[✓] Model saved to {MODEL_SAVE_PATH}")
    print("    Run `python manage.py rehash-model` to refresh the model hash.")


def _write_metrics(history):
    metrics = {k: [float(v) for v in vals] for k, vals in history.history.items()}
    metrics["class_names"] = CLASS_ORDER
    metrics["best_val_accuracy"] = max(metrics.get("val_accuracy", [0.0]))
    with open(METRICS_PATH, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)
    print(f"[✓] Metrics saved to {METRICS_PATH}")


def plot_history(history):
    os.makedirs("models", exist_ok=True)
    acc = history.history["accuracy"]
    val_acc = history.history["val_accuracy"]
    loss = history.history["loss"]
    val_loss = history.history["val_loss"]
    epochs_range = range(len(acc))
    plt.figure(figsize=(12, 5))
    plt.subplot(1, 2, 1)
    plt.plot(epochs_range, acc, label="Training Accuracy")
    plt.plot(epochs_range, val_acc, label="Validation Accuracy")
    plt.legend(loc="lower right")
    plt.title("Training and Validation Accuracy")
    plt.subplot(1, 2, 2)
    plt.plot(epochs_range, loss, label="Training Loss")
    plt.plot(epochs_range, val_loss, label="Validation Loss")
    plt.legend(loc="upper right")
    plt.title("Training and Validation Loss")
    plt.tight_layout()
    plt.savefig(PLOT_SAVE_PATH)


if __name__ == "__main__":
    train_model()
