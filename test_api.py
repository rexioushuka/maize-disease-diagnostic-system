import os
import requests

# API endpoint
URL = "http://127.0.0.1:5000/predict"

# Directory with test images
test_dir = "data/processed/test/MSV"

# Automatically find the first image file in the directory
images = [f for f in os.listdir(test_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]

if not images:
    print(f"[!] No image files found in '{test_dir}'")
else:
    sample_image_path = os.path.join(test_dir, images[0])
    print(f"[+] Testing API using: {sample_image_path}")

    try:
        with open(sample_image_path, "rb") as img_file:
            files = {"image": img_file}
            response = requests.post(URL, files=files)

        print("\n--- API RESPONSE ---")
        import json
        print(json.dumps(response.json(), indent=4))
    except Exception as e:
        print(f"[!] Error connecting to API: {e}")