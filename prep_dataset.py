"""Dataset preparation with NO train/test leakage (SEC-22).

Order of operations is critical:
1. Validate and collect ORIGINAL images per class.
2. Split originals into train/val/test FIRST (stratified, deterministic seed).
3. Augment ONLY the training split to reach the target size.

This guarantees the test set contains only unseen original images, so the
reported accuracy reflects generalisation instead of memorised augmentations.
"""

import hashlib
import json
import os
import random
import shutil

from PIL import Image, ImageEnhance, ImageOps
from tqdm import tqdm

RAW_DIR = "data/raw"
PROCESSED_DIR = "data/processed"
MAPPING_OUT = "models/class_mapping.json"
SEED = 42

# Target size for the TRAIN split after augmentation.
TRAIN_TARGET = 1750
# Balanced caps for held-out splits (originals only, never augmented).
VAL_PER_CLASS = 375
TEST_PER_CLASS = 375

CLASSES = {
    "healthy": "Healthy",
    "msv": "MSV",
    "mln": "MLN",
    "downy_mildew": "Downy_Mildew",
}
# Authoritative order == model output index. Never derive it from folder names.
CLASS_ORDER = ["Downy_Mildew", "Healthy", "MLN", "MSV"]


def is_valid_image(filepath):
    try:
        with Image.open(filepath) as img:
            img.verify()
        return True
    except Exception:
        return False


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def augment_image(pil_img):
    if random.random() > 0.5:
        pil_img = ImageOps.mirror(pil_img)
    if random.random() > 0.5:
        pil_img = ImageOps.flip(pil_img)
    angle = random.randint(-40, 40)
    pil_img = pil_img.rotate(angle, expand=False, fillcolor=(0, 0, 0))
    pil_img = ImageEnhance.Brightness(pil_img).enhance(random.uniform(0.75, 1.25))
    pil_img = ImageEnhance.Contrast(pil_img).enhance(random.uniform(0.8, 1.2))
    return pil_img


def create_split_directories():
    for split in ("train", "val", "test"):
        for label in CLASS_ORDER:
            os.makedirs(os.path.join(PROCESSED_DIR, split, label), exist_ok=True)


def collect_originals(source_path, limit):
    files = [
        f for f in os.listdir(source_path)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
        and os.path.basename(source_path) != "auto_augmented"
    ]
    random.Random(SEED).shuffle(files)
    valid = []
    for name in files:
        if is_valid_image(os.path.join(source_path, name)):
            valid.append(name)
            if limit and len(valid) >= limit:
                break
    return valid


def copy_files(source_path, files, split, label):
    for name in files:
        src = os.path.join(source_path, name)
        dst = os.path.join(PROCESSED_DIR, split, label, name)
        shutil.copy(src, dst)


def augment_train(source_path, train_files, target, label):
    needed = max(0, target - len(train_files))
    if needed == 0:
        return
    print(f"[+] Augmenting '{label}' train split with {needed} images")
    pbar = tqdm(total=needed, desc=f"Augment {label}")
    done = 0
    attempts = 0
    while done < needed and attempts < needed * 20:
        attempts += 1
        name = random.choice(train_files)
        try:
            with Image.open(os.path.join(source_path, name)) as img:
                aug = augment_image(img.convert("RGB"))
                out = f"aug_{done+1}_{name}"
                aug.save(os.path.join(PROCESSED_DIR, "train", label, out), "JPEG")
                done += 1
                pbar.update(1)
        except Exception:
            continue
    pbar.close()


def write_class_mapping():
    mapping = {
        "model_version": "MobileNetV2-v1.1",
        "file": "mobilenetv2_maize.keras",
        "sha256": "",
        "input_size": [224, 224],
        "class_names": CLASS_ORDER,
        "class_indices": {name: i for i, name in enumerate(CLASS_ORDER)},
        "notes": ("class_names order is authoritative; re-run manage.py "
                  "rehash-model after saving a new model."),
    }
    os.makedirs(os.path.dirname(MAPPING_OUT), exist_ok=True)
    with open(MAPPING_OUT, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, indent=2)
        fh.write("\n")
    print(f"[✓] Wrote class mapping to {MAPPING_OUT}")


def process_and_split_data():
    random.seed(SEED)
    create_split_directories()
    print("\n==========================================")
    print("      PHASE 1: DATASET PREPARATION       ")
    print("==========================================\n")
    summary = {}

    for raw_folder, label in CLASSES.items():
        source_path = os.path.join(RAW_DIR, raw_folder)
        if not os.path.exists(source_path):
            print(f"[!] Folder '{source_path}' not found! Skipping...")
            continue

        originals = collect_originals(source_path, None)
        random.Random(SEED).shuffle(originals)
        n = len(originals)
        if n == 0:
            print(f"[!] No valid images for '{label}'. Skipping.")
            continue

        # Split ORIGINALS first (SEC-22). Reserve up to VAL/TEST caps, keep the
        # rest for training, then cap train at TRAIN_TARGET.
        test_n = min(TEST_PER_CLASS, max(1, n // 5))
        val_n = min(VAL_PER_CLASS, max(1, n // 5))
        train_n = max(1, n - test_n - val_n)

        test_files = originals[:test_n]
        val_files = originals[test_n:test_n + val_n]
        train_orig = originals[test_n + val_n:]
        train_files = train_orig[:TRAIN_TARGET]

        copy_files(source_path, train_files, "train", label)
        copy_files(source_path, val_files, "val", label)
        copy_files(source_path, test_files, "test", label)
        augment_train(source_path, train_files, TRAIN_TARGET, label)

        summary[label] = {
            "originals": n,
            "train_originals": len(train_files),
            "val": len(val_files),
            "test": len(test_files),
        }
        print(f"[✓] '{label}': originals={n} train_orig={len(train_files)} "
              f"val={len(val_files)} test={len(test_files)}\n")

    with open("data/split_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    write_class_mapping()
    print("==========================================")
    print(" PHASE 1 COMPLETED (leak-aware splits) ")
    print("==========================================\n")


if __name__ == "__main__":
    process_and_split_data()
