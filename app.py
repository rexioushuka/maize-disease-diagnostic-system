import os
import io
import numpy as np
import tensorflow as tf
from PIL import Image
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

MODEL_PATH = "models/mobilenetv2_maize.h5"
IMG_SIZE = (224, 224)

print("[+] Loading trained MobileNetV2 model into memory...")
model = tf.keras.models.load_model(MODEL_PATH)
print("[✓] Model loaded successfully.")

CLASSES = {
    0: "Downy Mildew",
    1: "Healthy",
    2: "Maize Lethal Necrosis (MLN)",
    3: "Maize Streak Virus (MSV)"
}

ADVISORIES = {
    "Healthy": {
        "status": "Healthy Crop",
        "description": "The maize leaf shows no visible signs of infection.",
        "action": "Maintain normal field monitoring, weed control, and balanced fertilizer application."
    },
    "Maize Streak Virus (MSV)": {
        "status": "Viral Infection Detected",
        "description": "Characterized by yellow streaks parallel to leaf veins, caused by leafhopper vectors.",
        "action": "Control leafhopper population using recommended systemic insecticides. Remove severely infected plants early to prevent field spread."
    },
    "Maize Lethal Necrosis (MLN)": {
        "status": "Severe Viral Infection Detected",
        "description": "Result of co-infection by Maize Chlorotic Mottle Virus and Potyviruses, leading to leaf necrosis.",
        "action": "Uproot and burn infected crop residues immediately. Practice strict crop rotation with non-cereal crops."
    },
    "Downy Mildew": {
        "status": "Fungal Infection Detected",
        "description": "Characterized by whitish downy growth on leaves and yellowing leaf stripes.",
        "action": "Apply systemic fungicides (e.g., metalaxyl-based). Ensure proper field drainage and use certified disease-free seeds."
    }
}

def preprocess_image(image_bytes):
    img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    img = img.resize(IMG_SIZE)
    img_array = np.array(img, dtype=np.float32) / 255.0
    img_array = np.expand_dims(img_array, axis=0)
    return img_array

@app.route('/', methods=['GET'])
def index():
    """Renders the HTML Dashboard"""
    return render_template('index.html')

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "online", "model_loaded": model is not None}), 200

@app.route('/predict', methods=['POST'])
def predict():
    if 'image' not in request.files:
        return jsonify({"error": "No image file provided in request."}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({"error": "No file selected for uploading."}), 400

    try:
        image_bytes = file.read()
        processed_img = preprocess_image(image_bytes)

        predictions = model.predict(processed_img, verbose=0)[0]
        predicted_class_idx = int(np.argmax(predictions))
        confidence = float(predictions[predicted_class_idx]) * 100

        disease_name = CLASSES[predicted_class_idx]
        advisory_info = ADVISORIES[disease_name]

        response = {
            "success": True,
            "diagnosis": {
                "disease": disease_name,
                "confidence_score": f"{confidence:.2f}%",
                "confidence_raw": round(confidence, 4),
                "status": advisory_info["status"],
                "description": advisory_info["description"],
                "recommended_action": advisory_info["action"]
            },
            "all_probabilities": {
                CLASSES[i]: f"{float(prob) * 100:.2f}%" 
                for i, prob in enumerate(predictions)
            }
        }

        return jsonify(response), 200

    except Exception as e:
        return jsonify({"error": f"Failed to process image: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)