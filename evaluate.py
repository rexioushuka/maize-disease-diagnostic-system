import os
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import tensorflow as tf
from tensorflow.keras.preprocessing.image import ImageDataGenerator
from sklearn.metrics import classification_report, confusion_matrix

# --- CONFIGURATION ---
PROCESSED_DIR = "data/processed"
MODEL_PATH = "models/mobilenetv2_maize.h5"
CM_PLOT_PATH = "models/confusion_matrix.png"

IMG_SIZE = (224, 224)
BATCH_SIZE = 32

def evaluate_model():
    print("\n==========================================")
    print("      PHASE 3: MODEL EVALUATION          ")
    print("==========================================\n")

    # 1. Load Trained Model
    print(f"[+] Loading best model from '{MODEL_PATH}'...")
    model = tf.keras.models.load_model(MODEL_PATH)

    # 2. Load Unseen Test Set
    test_datagen = ImageDataGenerator(rescale=1.0 / 255.0)

    test_generator = test_datagen.flow_from_directory(
        os.path.join(PROCESSED_DIR, 'test'),
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode='categorical',
        shuffle=False  # Do NOT shuffle, so prediction indices match ground truth labels
    )

    class_labels = list(test_generator.class_indices.keys())

    # 3. Generate Predictions on Test Set
    print("\n[+] Running inference on unseen test images...")
    predictions = model.predict(test_generator, verbose=1)
    
    # Convert probabilities into class index predictions
    y_pred = np.argmax(predictions, axis=1)
    y_true = test_generator.classes

    # 4. Print Classification Report
    print("\n------------------------------------------")
    print("         CLASSIFICATION REPORT            ")
    print("------------------------------------------")
    report = classification_report(y_true, y_pred, target_names=class_labels, digits=4)
    print(report)

    # 5. Generate and Save Confusion Matrix Plot
    print("[+] Plotting Confusion Matrix Heatmap...")
    cm = confusion_matrix(y_true, y_pred)

    plt.figure(figsize=(8, 6))
    sns.heatmap(cm, annot=True, fmt='d', cmap='Blues',
                xticklabels=class_labels, yticklabels=class_labels)
    plt.title('Confusion Matrix - Maize Disease Diagnostic Model', fontsize=12)
    plt.ylabel('True Class', fontsize=10)
    plt.xlabel('Predicted Class', fontsize=10)
    plt.tight_layout()
    plt.savefig(CM_PLOT_PATH, dpi=300)

    print(f"[✓] Confusion matrix heatmap saved to '{CM_PLOT_PATH}'")
    print("\n==========================================")
    print(" PHASE 3 COMPLETED SUCCESSFULLY! ")
    print("==========================================\n")

if __name__ == "__main__":
    evaluate_model()