import os
import json
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from datetime import datetime
import openai
import boto3
from PIL import Image
from PIL.ExifTags import TAGS

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

# Load OpenAI key from environment (✅ no secrets in code)
openai.api_key = os.getenv("OPENAI_API_KEY")

# S3 config
s3 = boto3.client('s3',
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)
BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def extract_metadata(image_path):
    try:
        image = Image.open(image_path)
        exifdata = image.getexif()
        metadata = {}
        for tag_id, value in exifdata.items():
            tag = TAGS.get(tag_id, tag_id)
            metadata[tag] = str(value)
        return metadata
    except Exception as e:
        return {"error": str(e)}

def save_json_to_s3(data, filename_prefix):
    timestamp = datetime.utcnow().strftime('%Y%m%dT%H%M%S')
    filename = f"{filename_prefix}_{timestamp}.json"
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=filename,
        Body=json.dumps(data),
        ContentType='application/json'
    )

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'photo' not in request.files:
        return "No photo uploaded", 400
    file = request.files['photo']
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    return jsonify({"filename": filename})

@app.route('/submit', methods=['POST'])
def submit_file():
    filename = request.json.get('filename')
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    metadata = extract_metadata(filepath)

    prompt = f"""You're part of a system that interprets image metadata using a fixed 9-question yes/no framework.
Use this structure:

→ Born Real?
1️⃣ Was this photo taken with a real phone or camera?
2️⃣ Does it still have the original date and time?
3️⃣ Is the lighting and detail natural?

→ Left Untouched?
4️⃣ No filters or beauty tools added?
5️⃣ No cropping or visual editing?
6️⃣ Has it only been saved once — not re-exported?

→ Shared Naturally?
7️⃣ Is the original filename still intact?
8️⃣ Was it not reposted or downloaded from the internet?
9️⃣ Was it shared directly (like via AirDrop or text)?

Respond with YES or NO to each. Then give a final summary line explaining the authenticity.

Metadata:
{json.dumps(metadata, indent=2)}
"""

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a metadata interpreter."},
            {"role": "user", "content": prompt}
        ]
    )

    full_story_output = response['choices'][0]['message']['content']
    filename_prefix = filename.rsplit('.', 1)[0]
    save_json_to_s3({"filename": filename, "output": full_story_output}, filename_prefix)
    
    return jsonify({"output": full_story_output})

@app.route('/uploads/<filename>')
def serve_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

if __name__ == '__main__':
    app.run(debug=True)
