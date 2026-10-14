import asyncio
import os
import re
import time
import uuid

from flask import Flask, jsonify, request, send_from_directory, url_for

from pdf_cleanup import process_pdf
from tts_script import OUTPUT_DIR, VOICE, synthesize_to_files

PORT = 5001

# Generated audio is only needed until the browser has downloaded it (the
# browser keeps its own copy with each library entry), so the output folder is
# temporary: generated files older than this are deleted.
OUTPUT_MAX_AGE_SECONDS = 60 * 60
# Only files this server generates (uuid4 hex + suffix) are ever deleted, so
# test.mp3 / test_words.json and anything else placed there are left alone.
GENERATED_FILE_PATTERN = re.compile(r"^[0-9a-f]{32}(\.mp3|_words\.json)$")
PROTECTED_FILES = {"test.mp3", "test_words.json"}

app = Flask(__name__)
# Uploaded PDFs are read in memory; larger requests get a 413 from Flask.
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024


def cleanup_old_outputs(max_age_seconds=OUTPUT_MAX_AGE_SECONDS):
    if not os.path.isdir(OUTPUT_DIR):
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for name in os.listdir(OUTPUT_DIR):
        if name in PROTECTED_FILES or not GENERATED_FILE_PATTERN.match(name):
            continue
        path = os.path.join(OUTPUT_DIR, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except FileNotFoundError:
            pass  # removed by a concurrent request's cleanup
        except OSError as error:
            app.logger.warning("Could not delete old output %s: %s", name, error)
    if removed:
        app.logger.info("Deleted %d generated file(s) older than %ds", removed, max_age_seconds)
    return removed


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response


@app.route("/synthesize", methods=["POST", "OPTIONS"])
def synthesize():
    if request.method == "OPTIONS":
        return ""

    data = request.get_json(silent=True) or {}
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "JSON body must include a non-empty 'text' string"}), 400

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    cleanup_old_outputs()
    request_id = uuid.uuid4().hex
    mp3_filename = f"{request_id}.mp3"
    words_filename = f"{request_id}_words.json"
    mp3_path = os.path.join(OUTPUT_DIR, mp3_filename)
    words_path = os.path.join(OUTPUT_DIR, words_filename)

    words = asyncio.run(synthesize_to_files(text, mp3_path, words_path, voice=VOICE))

    audio_url = url_for("output_file", filename=mp3_filename, _external=True)
    return jsonify({"audio_url": audio_url, "words": words})


@app.route("/extract-pdf", methods=["POST", "OPTIONS"])
def extract_pdf_route():
    """Multipart upload with the PDF in a "file" field; returns the extracted,
    cleaned-up text (pdf_cleanup.process_pdf()), ready for /synthesize.
    Display equations are always left out (listed in "equations"); in-text
    citations are removed only when the form field strip_citations is
    "1"/"true"/"yes"/"on" (listed in "citations")."""
    if request.method == "OPTIONS":
        return ""

    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "Upload the PDF as multipart form data in a 'file' field"}), 400
    data = upload.read()
    if not data.startswith(b"%PDF-"):
        return jsonify({"error": "The uploaded file isn't a PDF"}), 400

    try:
        strip = request.form.get("strip_citations", "").lower() in ("1", "true", "yes", "on")
        result = process_pdf(data, strip_citations=strip)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify(result)


@app.route("/output/<path:filename>")
def output_file(filename):
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == "__main__":
    cleanup_old_outputs()
    app.run(host="127.0.0.1", port=PORT)
