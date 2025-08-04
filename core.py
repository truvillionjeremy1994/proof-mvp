import os
import json
import uuid
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

    s3_key = f"temp/{filename}"
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=s3_key,
        Body=file,
        ContentType=file.content_type
    )

    s3_url = f"https://{BUCKET_NAME}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{s3_key}"
    return jsonify({"filename": filename, "url": s3_url})

@app.route('/submit', methods=['POST'])
def submit_file():
    filename = request.form.get('filename')
    file = request.files.get('photo')
    s3_url = request.form.get('url')

    if not file or not filename:
        return jsonify({"error": "Missing file or filename"}), 400

    metadata = extract_metadata(file.stream)

    system_prompt = """
Return only valid JSON. Do not explain anything. Group exactly 9 questions into 3 labeled sections: 'born_real', 'left_untouched', and 'shared_naturally'. Each section must include exactly 3 items, formatted as [question_text, true|false]. Also include: final_verdict (emoji + text), yes_count (0–9), no_count (0–9), and a response that’s 30 words or less, neutral, and tells a story about that photo's life in JSON only. No markdown. No extra commentary.
"""

    user_prompt = f"""
Use the metadata below to answer the following 9 questions.

→ Born Real?
1. Was this photo taken with a real phone or camera?
2. Does it still have the original date and time?
3. Is the lighting and detail natural?

→ Left Untouched?
4. No filters or beauty tools added?
5. No cropping or visual editing?
6. Has it only been saved once — not re-exported?

→ Shared Naturally?
7. Is the original filename still intact?
8. Was it not reposted or downloaded from the internet?
9. Was it shared directly (like via AirDrop or text)?

Respond only in JSON using the required format.

Metadata:
{json.dumps(metadata, indent=2)}
"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )

    content = response.choices[0].message.content.strip()
    try:
        result_json = json.loads(content)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON returned from GPT"}), 500

    result_json["answers"] = {
        "born_real": result_json.pop("born_real", []),
        "left_untouched": result_json.pop("left_untouched", []),
        "shared_naturally": result_json.pop("shared_naturally", [])
    }

    result_json["filename"] = filename
    result_json["url"] = s3_url

    save_json_to_s3({"filename": filename, "result": result_json}, filename.rsplit(".", 1)[0])
    return jsonify({"success": True, "result": result_json})

@app.route('/count', methods=['GET'])
def count():
    try:
        response = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix='logs/')
        files = response.get('Contents', [])
        json_files = [f for f in files if f['Key'].endswith('.json')]
        return jsonify({"count": len(json_files)})
    except Exception as e:
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