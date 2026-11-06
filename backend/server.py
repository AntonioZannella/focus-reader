import asyncio
from collections import deque
import ipaddress
import math
import threading
import os
import re
import time
import uuid

from flask import Flask, jsonify, request, send_from_directory, url_for
from werkzeug.exceptions import HTTPException

from pdf_cleanup import process_pdf
from tts_script import OUTPUT_DIR, VOICE, VOICES, synthesize_to_files

# 5001 locally (python backend/server.py); a host such as Hugging Face sets
# PORT itself (7860 in the Docker image, where gunicorn binds it instead).
PORT = int(os.environ.get("PORT", "5001"))
# The page sends sections of at most ~600 words (a few thousand characters),
# so anything longer than this isn't the app and is refused: the site is
# public and each request costs a call to the speech service.
MAX_SYNTHESIS_CHARS = 10_000
SPEECH_REQUESTS_PER_MINUTE = int(os.environ.get("SPEECH_REQUESTS_PER_MINUTE", "30"))
if SPEECH_REQUESTS_PER_MINUTE < 1:
    raise ValueError("SPEECH_REQUESTS_PER_MINUTE must be positive")


class SpeechLimiter:
    # In-memory counts are per process and reset on restart. Multiple workers
    # or instances need a shared store before this is a deployment-wide limit.
    def __init__(self, limit=SPEECH_REQUESTS_PER_MINUTE, window=60, max_clients=10_000):
        self.limit, self.window, self.max_clients = limit, window, max_clients
        self.clients = {}
        self.lock = threading.Lock()

    def retry_after(self, client):
        now = time.monotonic()
        with self.lock:
            for key, times in list(self.clients.items()):
                while times and times[0] <= now - self.window:
                    times.popleft()
                if not times:
                    del self.clients[key]
            if client not in self.clients:
                if len(self.clients) >= self.max_clients:
                    return self.window  # Fail closed rather than evict an active limit.
                self.clients[client] = deque()
            times = self.clients[client]
            if len(times) >= self.limit:
                return max(1, math.ceil(times[0] + self.window - now))
            times.append(now)
        return 0


speech_limiter = SpeechLimiter()


def speech_client_ip():
    # Render's public ingress passes through Cloudflare. Trust its single-IP
    # header only on Render, never a caller-supplied leftmost X-Forwarded-For.
    # Direct/local requests use the socket peer. Missing proxy metadata shares
    # the peer's quota conservatively; verify headers if adding another proxy.
    candidate = request.remote_addr or "unknown"
    if os.environ.get("RENDER") == "true":
        candidate = request.headers.get("CF-Connecting-IP", candidate)
    try:
        address = ipaddress.ip_address(candidate)
        return str(address.ipv4_mapped or address) if address.version == 6 else str(address)
    except ValueError:
        return "unknown"


def limit_speech():
    retry = speech_limiter.retry_after(speech_client_ip())
    if retry:
        response = jsonify({"error": "Too many speech requests. Please try again shortly."})
        response.status_code = 429
        response.headers["Retry-After"] = str(retry)
        return response

# Largest PDF accepted; the page's MAX_PDF_BYTES must be the same.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Generated audio is only needed until the browser has downloaded it (the
# browser keeps its own copy with each library entry), so the output folder is
# temporary: generated files older than this are deleted.
OUTPUT_MAX_AGE_SECONDS = 60 * 60
# Only files this server generates (uuid4 hex + suffix) are ever deleted, so
# test.mp3 / test_words.json and anything else placed there are left alone.
GENERATED_FILE_PATTERN = re.compile(r"^[0-9a-f]{32}(\.mp3|_words\.json)$")
PROTECTED_FILES = {"test.mp3", "test_words.json"}
# The page and the files it loads (alignment.js, words.txt...) are served from
# frontend/, found from this file's location so the server works whichever
# folder it's started from. send_from_directory refuses any path that would
# leave that folder.
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "").rstrip("/")

app = Flask(__name__)
# Uploaded PDFs are read in memory; larger requests get a 413 (as JSON, below).
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


@app.errorhandler(413)
def request_too_large(error):
    return jsonify({"error": f"The upload is too large; the limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."}), 413


@app.errorhandler(HTTPException)
def http_error(error):
    # Keep status and protocol headers (for example Allow on a 405).
    response = error.get_response()
    response.set_data(app.json.dumps({"error": error.description}))
    response.content_type = "application/json"
    return response


@app.errorhandler(Exception)
def unexpected_error(error):
    app.logger.exception("Unhandled request failure")
    return jsonify({"error": "An internal error occurred. Please try again."}), 500


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
        except OSError:
            app.logger.exception("Could not delete old output %s", name)
    if removed:
        app.logger.info("Deleted %d generated file(s) older than %ds", removed, max_age_seconds)
    return removed


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    configured = ALLOWED_ORIGIN.startswith(("https://", "http://")) and origin == ALLOWED_ORIGIN
    local = re.fullmatch(r"https?://(?:localhost|127\.0\.0\.1)(?::[0-9]{1,5})?", origin)
    if origin and (configured or local):
        response.headers["Access-Control-Allow-Origin"] = origin
    response.vary.add("Origin")
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response


@app.route("/synthesize", methods=["POST", "OPTIONS"])
def synthesize():
    if request.method == "OPTIONS":
        return ""

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON body must be an object"}), 400
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "JSON body must include a non-empty 'text' string"}), 400
    voice = data.get("voice", VOICE)
    if not isinstance(voice, str):
        return jsonify({"error": "voice must be a string"}), 400
    if voice not in VOICES:
        return jsonify({"error": f"Unknown voice {voice!r}"}), 400
    if len(text) > MAX_SYNTHESIS_CHARS:
        return jsonify({"error": f"This text is too long to read in one request ({len(text):,} characters; the limit is {MAX_SYNTHESIS_CHARS:,})."}), 413

    limited = limit_speech()
    if limited is not None:
        return limited

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    cleanup_old_outputs()
    request_id = uuid.uuid4().hex
    mp3_filename = f"{request_id}.mp3"
    words_filename = f"{request_id}_words.json"
    mp3_path = os.path.join(OUTPUT_DIR, mp3_filename)
    words_path = os.path.join(OUTPUT_DIR, words_filename)

    # asyncio.run makes a new event loop for this call and closes it after, so
    # it's safe in gunicorn's worker threads (none of them has a loop running)
    try:
        words = asyncio.run(synthesize_to_files(text, mp3_path, words_path, voice=voice))
    except Exception:  # every retry failed: the speech service is down or refusing
        app.logger.exception("Speech synthesis failed for %d characters", len(text))
        for path in (mp3_path, words_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass  # The service may fail before creating either file.
            except OSError:
                app.logger.exception("Could not remove failed synthesis output %s", path)
        return jsonify({"error": "The speech service didn't respond. Check your internet connection and try again."}), 502

    # a relative link ("/output/<id>.mp3"): an absolute http:// one would be
    # blocked as mixed content when the site is served over https by a proxy
    audio_url = url_for("output_file", filename=mp3_filename)
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


# Voice samples ("Hi, I'm Ava.") for the Voice buttons: made once per voice
# and kept on disk (a subfolder, so the hourly cleanup of generated files
# leaves them alone), then served straight away on every later request.

@app.route("/voice-sample/<voice>")
def voice_sample(voice):
    if voice not in VOICES:
        return jsonify({"error": f"Unknown voice {voice!r}"}), 404
    sample_dir = os.path.join(OUTPUT_DIR, "voice-samples")
    filename = f"{voice}.mp3"
    if not os.path.isfile(os.path.join(sample_dir, filename)):
        limited = limit_speech()
        if limited is not None:
            return limited
        os.makedirs(sample_dir, exist_ok=True)
        # made under a unique name and moved into place, so two first requests
        # at once never serve a half-written file
        partial = os.path.join(sample_dir, f"{uuid.uuid4().hex}.partial")
        try:
            asyncio.run(synthesize_to_files(f"Hi, I'm {VOICES[voice]}.", partial, partial + ".json", voice=voice))
            os.replace(partial, os.path.join(sample_dir, filename))
        except Exception:
            app.logger.exception("Voice sample for %s failed", voice)
            return jsonify({"error": "The speech service didn't respond."}), 502
        finally:
            for leftover in (partial, partial + ".json"):
                try:
                    os.remove(leftover)
                except FileNotFoundError:
                    pass  # Another request or os.replace already removed it.
                except OSError:
                    app.logger.exception("Could not remove voice sample temporary file %s", leftover)
    response = send_from_directory(sample_dir, filename)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def frontend_file(filename):
    return send_from_directory(FRONTEND_DIR, filename)


if __name__ == "__main__":
    cleanup_old_outputs()
    app.run(host="127.0.0.1", port=PORT)
