import os
import matplotlib.pyplot as plt
import tensorflow as tf
from tensorflow.keras.applications import MobileNetV2
from tensorflow.keras.layers import Dense, GlobalAveragePooling2D, Dropout
from tensorflow.keras.models import Model
from tensorflow.keras.preprocessing.image import ImageDataGenerator
from tensorflow.keras.callbacks import ModelCheckpoint, EarlyStopping

# --- CONFIGURATION ---
PROCESSED_DIR = "data/processed"
MODEL_SAVE_PATH = "models/mobilenetv2_maize.h5"
PLOT_SAVE_PATH = "models/training_history.png"

IMG_SIZE = (224, 224)
BATCH_SIZE = 32
EPOCHS = 15

def train_model():
    print("\n==========================================")
    print("      PHASE 2: MODEL TRAINING (CNN)       ")
    print("==========================================\n")

    # 1. Prepare Data Generators with Data Augmentation
    print("[+] Loading and scaling image batches...")
    
    train_datagen = ImageDataGenerator(
        rescale=1.0 / 255.0,
        rotation_range=20,
        width_shift_range=0.1,
        height_shift_range=0.1,
        zoom_range=0.15,
        horizontal_flip=True,
        fill_mode='nearest'
    )

    val_datagen = ImageDataGenerator(rescale=1.0 / 255.0)

    train_generator = train_datagen.flow_from_directory(
        os.path.join(PROCESSED_DIR, 'train'),
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode='categorical',
        shuffle=True
    )

    val_generator = val_datagen.flow_from_directory(
        os.path.join(PROCESSED_DIR, 'val'),
        target_size=IMG_SIZE,
        batch_size=BATCH_SIZE,
        class_mode='categorical',
        shuffle=False
    )

    # Print class index mapping for verification
    print(f"\n[✓] Detected Class Index Mapping: {train_generator.class_indices}\n")

    # 2. Build MobileNetV2 Architecture
    print("[+] Building MobileNetV2 Transfer Learning Architecture...")
    
    base_model = MobileNetV2(
        weights='imagenet', 
        include_top=False, 
        input_shape=(224, 224, 3)
    )
    
    # Freeze the base pre-trained layers
    base_model.trainable = False

    # Attach custom classification head
    x = base_model.output
    x = GlobalAveragePooling2D()(x)
    x = Dropout(0.3)(x)  # Prevents overfitting
    x = Dense(128, activation='relu')(x)
    predictions = Dense(4, activation='softmax')(x)  # 4 Output classes

    model = Model(inputs=base_model.input, outputs=predictions)

    # 3. Compile Model
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.0001),
        loss='categorical_crossentropy',
        metrics=['accuracy']
    )

    # 4. Define Training Callbacks
    callbacks = [
        ModelCheckpoint(
            MODEL_SAVE_PATH, 
            monitor='val_accuracy', 
            save_best_only=True, 
            verbose=1
        ),
        EarlyStopping(
            monitor='val_loss', 
            patience=4, 
            restore_best_weights=True, 
            verbose=1
        )
    ]

    # 5. Start Model Training
    print("[+] Starting Model Training...\n")
    history = model.fit(
        train_generator,
        epochs=EPOCHS,
        validation_data=val_generator,
        callbacks=callbacks
    )

    # 6. Save Training History Plot (Useful for research report)
    plot_history(history)
    
    print("\n==========================================")
    print(f" MODEL SAVED SUCCESSFULLY TO: {MODEL_SAVE_PATH} ")
    print("==========================================\n")

def plot_history(history):
    """Generates and saves accuracy/loss graph for academic report."""
    os.makedirs("models", exist_ok=True)
    
    acc = history.history['accuracy']
    val_acc = history.history['val_accuracy']
    loss = history.history['loss']
    val_loss = history.history['val_loss']
    epochs_range = range(len(acc))

    plt.figure(figsize=(12, 5))

    # Accuracy Plot
    plt.subplot(1, 2, 1)
    plt.plot(epochs_range, acc, label='Training Accuracy')
    plt.plot(epochs_range, val_acc, label='Validation Accuracy')
    plt.legend(loc='lower right')
    plt.title('Training and Validation Accuracy')

    # Loss Plot
    plt.subplot(1, 2, 2)
    plt.plot(epochs_range, loss, label='Training Loss')
    plt.plot(epochs_range, val_loss, label='Validation Loss')
    plt.legend(loc='upper right')
    plt.title('Training and Validation Loss')

    plt.tight_layout()
    plt.savefig(PLOT_SAVE_PATH)
    print(f"[✓] Accuracy/Loss graph saved to '{PLOT_SAVE_PATH}'")

if __name__ == "__main__":
    train_model()