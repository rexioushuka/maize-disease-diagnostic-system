import os
import shutil
import random
from PIL import Image, ImageOps, ImageEnhance
from tqdm import tqdm

# --- CONFIGURATION ---
RAW_DIR = "data/raw"
PROCESSED_DIR = "data/processed"

TARGET_SAMPLES_PER_CLASS = 2500  

CLASSES = {
    "healthy": "Healthy",
    "msv": "MSV",
    "mln": "MLN",
    "downy_mildew": "Downy_Mildew"
}

def is_valid_image(filepath):
    """
    Verifies if an image file is uncorrupted and readable.
    """
    try:
        with Image.open(filepath) as img:
            img.verify()
        return True
    except Exception:
        return False

def augment_image(pil_img):
    """
    Applies random transformations for data augmentation:
    - Flips, Rotations, Brightness, and Contrast adjustments
    """
    if random.random() > 0.5:
        pil_img = ImageOps.mirror(pil_img)
    if random.random() > 0.5:
        pil_img = ImageOps.flip(pil_img)

    angle = random.randint(-40, 40)
    pil_img = pil_img.rotate(angle, expand=False, fillcolor=(0, 0, 0))

    brightness_factor = random.uniform(0.75, 1.25)
    pil_img = ImageEnhance.Brightness(pil_img).enhance(brightness_factor)

    contrast_factor = random.uniform(0.8, 1.2)
    pil_img = ImageEnhance.Contrast(pil_img).enhance(contrast_factor)

    return pil_img

def auto_augment_class(source_path, valid_files, target_count, class_label):
    """
    Generates synthetic augmented images for small datasets (e.g. Downy Mildew)
    until target_count is achieved.
    """
    current_count = len(valid_files)
    needed_count = target_count - current_count
    
    print(f"[+] Class '{class_label}' has only {current_count} images. Generating {needed_count} augmented images...")

    aug_save_dir = os.path.join(source_path, "auto_augmented")
    os.makedirs(aug_save_dir, exist_ok=True)

    augmented_files = []
    pbar = tqdm(total=needed_count, desc=f"Augmenting {class_label}")

    count = 0
    while count < needed_count:
        base_filename = random.choice(valid_files)
        base_filepath = os.path.join(source_path, base_filename)

        try:
            with Image.open(base_filepath) as img:
                img = img.convert("RGB")
                aug_img = augment_image(img)
                
                aug_filename = f"aug_{count+1}_{os.path.basename(base_filename)}"
                aug_filepath = os.path.join(aug_save_dir, aug_filename)
                aug_img.save(aug_filepath, "JPEG")
                
                augmented_files.append(os.path.join("auto_augmented", aug_filename))
                count += 1
                pbar.update(1)
        except Exception:
            continue

    pbar.close()
    return valid_files + augmented_files

def get_fast_valid_samples(source_path, raw_files, target_count):
    """
    LAZY VALIDATION:
    Shuffles files first and stops validating as soon as target_count is reached.
    Prevent scanning 90,000+ unnecessary files.
    """
    shuffled_files = raw_files.copy()
    random.shuffle(shuffled_files)
    
    valid_files = []
    for file in shuffled_files:
        file_path = os.path.join(source_path, file)
        if is_valid_image(file_path):
            valid_files.append(file)
            if len(valid_files) >= target_count:
                break  # Stop early!
    return valid_files

def create_split_directories():
    for split in ['train', 'val', 'test']:
        for _, class_label in CLASSES.items():
            os.makedirs(os.path.join(PROCESSED_DIR, split, class_label), exist_ok=True)

def process_and_split_data():
    create_split_directories()
    print("\n==========================================")
    print("      PHASE 1: DATASET PREPARATION       ")
    print("==========================================\n")

    for raw_folder, class_label in CLASSES.items():
        source_path = os.path.join(RAW_DIR, raw_folder)
        
        if not os.path.exists(source_path):
            print(f"[!] Folder '{source_path}' not found! Skipping...")
            continue

        raw_files = [
            f for f in os.listdir(source_path) 
            if f.lower().endswith(('.png', '.jpg', '.jpeg'))
        ]
        
        print(f"Scanning '{class_label}'... Found {len(raw_files)} base images.")

        # Fast lazy collect up to 2,500 images
        valid_files = get_fast_valid_samples(source_path, raw_files, TARGET_SAMPLES_PER_CLASS)

        # Handle Downy Mildew or under-represented classes with auto-augmentation
        if len(valid_files) < TARGET_SAMPLES_PER_CLASS:
            selected_files = auto_augment_class(
                source_path, valid_files, TARGET_SAMPLES_PER_CLASS, class_label
            )
        else:
            selected_files = valid_files[:TARGET_SAMPLES_PER_CLASS]

        random.shuffle(selected_files)

        # 70% Train (1,750), 15% Val (375), 15% Test (375)
        train_end = int(0.70 * TARGET_SAMPLES_PER_CLASS)
        val_end = int(0.85 * TARGET_SAMPLES_PER_CLASS)

        train_files = selected_files[:train_end]
        val_files = selected_files[train_end:val_end]
        test_files = selected_files[val_end:]

        splits = {
            'train': train_files,
            'val': val_files,
            'test': test_files
        }

        for split_name, files in splits.items():
            for fname in files:
                src = os.path.join(source_path, fname)
                dst = os.path.join(PROCESSED_DIR, split_name, class_label, os.path.basename(fname))
                shutil.copy(src, dst)

        print(f"[✓] Completed '{class_label}': Train={len(train_files)}, Val={len(val_files)}, Test={len(test_files)}\n")

    print("==========================================")
    print(" PHASE 1 COMPLETED SUCCESSFULLY! ")
    print("==========================================\n")

if __name__ == "__main__":
    process_and_split_data()