import os
import json
import uuid
import re
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from datetime import datetime
from openai import OpenAI
import boto3
from PIL import Image
from PIL.ExifTags import TAGS

app = Flask(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

s3 = boto3.client('s3',
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)
BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")

def extract_metadata(file_stream):
    try:
        image = Image.open(file_stream)
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
    session_id = str(uuid.uuid4())
    data["timestamp"] = timestamp
    data["session_id"] = session_id
    filename = f"logs/{filename_prefix}_{timestamp}.json"

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

    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=f"temp/{filename}",
        Body=file,
        ContentType=file.content_type,
        ACL='public-read'
    )

    s3_url = f"https://{BUCKET_NAME}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/temp/{filename}"
    return jsonify({"filename": filename, "url": s3_url})

@app.route('/submit', methods=['POST'])
def submit_file():
    filename = request.form.get('filename')
    file = request.files.get('photo')
    s3_url = request.form.get('url')

    if not file or not filename:
        return jsonify({"error": "Missing file or filename"}), 400

    metadata = extract_metadata(file.stream)

    prompt = f"""You're part of a system that interprets image metadata using a fixed 9-question yes/no framework.
Use this structure:

→ Born Real?
1⃣ Was this photo taken with a real phone or camera?
2⃣ Does it still have the original date and time?
3⃣ Is the lighting and detail natural?

→ Left Untouched?
4⃣ No filters or beauty tools added?
5⃣ No cropping or visual editing?
6⃣ Has it only been saved once — not re-exported?

→ Shared Naturally?
7⃣ Is the original filename still intact?
8⃣ Was it not reposted or downloaded from the internet?
9⃣ Was it shared directly (like via AirDrop or text)?

Respond exactly like this:
→ Born Real?
1⃣ Question → ✅ Yes / ❌ No
...
Summary: [brief summary based on metadata]

Metadata:
{json.dumps(metadata, indent=2)}
"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a metadata interpreter."},
            {"role": "user", "content": prompt}
        ]
    )

    full_story_output = response.choices[0].message.content
    filename_prefix = filename.rsplit('.', 1)[0]

    answers = {
        "born_real": [],
        "left_untouched": [],
        "shared_naturally": []
    }

    lines = full_story_output.splitlines()
    current_group = None
    group_map = {
        "→ Born Real?": "born_real",
        "→ Left Untouched?": "left_untouched",
        "→ Shared Naturally?": "shared_naturally"
    }

    for line in lines:
        line = line.strip()
        if line in group_map:
            current_group = group_map[line]
        elif current_group and '→' in line:
            parts = line.split('→')
            if len(parts) == 2:
                question = parts[0].strip()
                answer = parts[1].strip()
                answers[current_group].append([question, answer == '✅ Yes'])

    result = {
        "answers": answers,
        "yes_count": full_story_output.count("✅ Yes"),
        "no_count": full_story_output.count("❌ No"),
        "response": full_story_output.strip(),
        "filename": filename,
        "url": s3_url
    }

    save_json_to_s3({"filename": filename, "result": result}, filename_prefix)

    return jsonify({"success": True, "result": result})

@app.route('/count', methods=['GET'])
def count():
    try:
        response = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix='logs/')
        files = response.get('Contents', [])
        json_files = [f for f in files if f['Key'].endswith('.json')]
        return jsonify({"count": len(json_files)})
    except Exception as e:
        print("Error in /count:", e)
        return jsonify({"error": str(e)}), 500

@app.route('/uploads/<filename>')
def serve_file(filename):
    return send_from_directory('static', filename)

@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/results.html')
def results_page():
    return send_from_directory('templates', 'results.html')

if __name__ == '__main__':
    app.run(debug=True)