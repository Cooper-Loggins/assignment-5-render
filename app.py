import io
import json
import os
import re
import urllib.request
import wave
from datetime import datetime
from functools import wraps
from hmac import compare_digest

from flask import Flask, Response, jsonify, render_template, request, send_file, session
from flask_sock import Sock
from google import genai

import db

SAMPLE_RATE = 16000
MIN_AUDIO_BYTES = SAMPLE_RATE
LLM_MODEL = "gemini-3.1-flash-lite-preview"
SYSTEM_PROMPT = (
    "You are a helpful smart assistant for a small wearable screen. "
    "Be concise, practical, and under 80 words."
)
AUDIO_SUBDIR = "audio"


def load_env_file(path=".env"):
    env_path = os.path.join(os.path.dirname(__file__), path)
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            os.environ.setdefault(key, value)


load_env_file()


def parse_last_json(text):
    decoder = json.JSONDecoder()
    last = None
    pos = 0
    text = text.strip()
    while pos < len(text):
        try:
            obj, end = decoder.raw_decode(text, pos)
            last = obj
            pos = end
            while pos < len(text) and text[pos] in " \t\n\r":
                pos += 1
        except json.JSONDecodeError:
            break
    return last or {}


def ensure_audio_dir():
    media_root = os.path.join(os.path.dirname(__file__), "media", AUDIO_SUBDIR)
    os.makedirs(media_root, exist_ok=True)
    return media_root


def build_audio_filename(source="device"):
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    safe_source = re.sub(r"[^a-z0-9_-]+", "-", (source or "device").lower()).strip("-")
    safe_source = safe_source or "device"
    return f"{safe_source}-{stamp}.wav"


def save_pcm_wav(audio_bytes, source="device"):
    audio_dir = ensure_audio_dir()
    filename = build_audio_filename(source)
    full_path = os.path.join(audio_dir, filename)
    with wave.open(full_path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(audio_bytes)
    return os.path.join(AUDIO_SUBDIR, filename)


def resolve_audio_path(relative_path):
    return os.path.join(os.path.dirname(__file__), "media", relative_path)


def read_uploaded_audio(upload):
    if upload is None or not upload.filename:
        raise ValueError("audio file is required")

    payload = upload.read()
    if not payload:
        raise ValueError("audio file is empty")

    content_type = (upload.content_type or "").lower()
    filename = upload.filename.lower()

    if payload[:4] == b"RIFF" or content_type in {"audio/wav", "audio/x-wav"} or filename.endswith(".wav"):
        with wave.open(io.BytesIO(payload), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())
        if channels != 1 or sample_width != 2 or sample_rate != SAMPLE_RATE:
            raise ValueError("audio must be 16-bit mono WAV at 16000 Hz")
        return frames

    return payload


def transcribe_audio(audio_bytes):
    wit_token = os.environ.get("WIT_TOKEN")
    if not wit_token:
        return "(transcription unavailable: set WIT_TOKEN)"

    req = urllib.request.Request(
        "https://api.wit.ai/speech?v=20240101",
        data=audio_bytes,
        headers={
            "Authorization": f"Bearer {wit_token}",
            "Content-Type": "audio/raw;encoding=signed-integer;bits=16;rate=16000;endian=little",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
    result = parse_last_json(body)
    return result.get("text", "").strip() or "(no speech detected)"


def local_summary(text):
    clean = " ".join(text.split())
    if len(clean) <= 80:
        return clean
    return clean[:77].rstrip() + "..."


def get_llm_client():
    api_key = os.environ.get("VERTEX_API_KEY")
    if not api_key:
        return None
    return genai.Client(vertexai=True, api_key=api_key)


def extract_json_object(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def analyze_voice_note(transcript):
    clean = " ".join(transcript.split())
    fallback = {
        "summary": local_summary(clean),
        "todo_title": None,
    }

    if len(clean.split()) < 8:
        return fallback

    client = get_llm_client()
    if client is None:
        return fallback

    prompt = (
        "Analyze this voice note. Return strict JSON with exactly two keys: "
        '"summary" and "todo_title". '
        '"summary" must be a concise dashboard-ready summary under 90 characters. '
        '"todo_title" must be either a short actionable todo title under 70 characters '
        'or null if the note does not clearly imply a task.\n\n'
        f"Voice note:\n{clean}"
    )

    try:
        response = client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                system_instruction=(
                    "You extract concise summaries and possible follow-up tasks from notes. "
                    "Always return valid JSON only."
                ),
            ),
        )
    except Exception:
        return fallback

    parsed = extract_json_object((response.text or "").strip())
    summary = (parsed.get("summary") or "").strip()
    todo_title = parsed.get("todo_title")
    if isinstance(todo_title, str):
        todo_title = todo_title.strip() or None
    else:
        todo_title = None

    return {
        "summary": summary or fallback["summary"],
        "todo_title": todo_title,
    }


def maybe_create_todo(transcript):
    lowered = " ".join(transcript.lower().strip().split())
    prefixes = [
        "todo ",
        "todo:",
        "to do ",
        "to do:",
        "to-do ",
        "to-do:",
        "add todo ",
        "add a todo ",
        "add to do ",
        "add a to do ",
        "remember to ",
        "remind me to ",
        "i need to ",
        "don't let me forget to ",
        "do not let me forget to ",
    ]
    for prefix in prefixes:
        if lowered.startswith(prefix):
            title = transcript[len(prefix):].strip(" .:")
            if title:
                return db.insert_todo(title)
    return None


def build_fallback_response(transcript, created_todo):
    if created_todo:
        return f"Added to your to-do list: {created_todo['title']}"

    open_todos = db.fetch_todos(limit=3, include_completed=False)
    if open_todos:
        preview = ", ".join(item["title"] for item in open_todos)
        return f"Saved your note. Current to-dos: {preview}"

    return "Saved your note."


def process_audio_note(audio_bytes, source="device"):
    audio_path = save_pcm_wav(audio_bytes, source=source)

    try:
        transcript = transcribe_audio(audio_bytes)
    except Exception as exc:
        transcript = f"(transcription error: {exc})"

    note_analysis = analyze_voice_note(transcript)
    summary = note_analysis["summary"]
    extracted_todo_title = note_analysis["todo_title"]

    created_todo = maybe_create_todo(transcript)
    if created_todo is None and extracted_todo_title:
        created_todo = db.insert_todo(extracted_todo_title)

    note = db.insert_note(
        transcript=transcript,
        summary=summary,
        audio_path=audio_path,
        source=source,
    )

    return {
        "audio_path": audio_path,
        "created_todo": created_todo,
        "note": note,
        "summary": summary,
        "transcript": transcript,
    }


def generate_assistant_response(transcript, created_todo):
    client = get_llm_client()
    if client is None:
        return build_fallback_response(transcript, created_todo)

    prompt = transcript
    if created_todo:
        prompt = (
            f"User said: {transcript}\n"
            f"A todo was already created with title: {created_todo['title']}.\n"
            "Acknowledge that briefly."
        )

    response = client.models.generate_content(
        model=LLM_MODEL,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    text = (response.text or "").strip()
    return text or build_fallback_response(transcript, created_todo)


def dashboard_unauthorized():
    return Response(
        "Dashboard authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="Assignment 5 Dashboard"'},
    )


def dashboard_auth_is_valid():
    if session.get("dashboard_authenticated") is True:
        return True

    auth = request.authorization
    expected_user = os.environ.get("DASHBOARD_USERNAME", "admin")
    expected_password = os.environ.get("DASHBOARD_PASSWORD", "replace_me")

    if auth is None or auth.username is None or auth.password is None:
        return False

    return compare_digest(auth.username, expected_user) and compare_digest(
        auth.password, expected_password
    )


def require_dashboard_auth(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not dashboard_auth_is_valid():
            return dashboard_unauthorized()
        return view_func(*args, **kwargs)

    return wrapped


def device_api_key_is_valid():
    expected_key = os.environ.get("DEVICE_API_KEY", "replace_me")
    provided_key = request.headers.get("X-Device-API-Key", "") or request.args.get(
        "api_key", ""
    )
    return compare_digest(provided_key, expected_key)


def device_unauthorized():
    return jsonify({"status": "error", "message": "invalid device api key"}), 401


def device_or_dashboard_auth_is_valid():
    return device_api_key_is_valid() or dashboard_auth_is_valid()


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("APP_SECRET_KEY", "dev-secret")
    app.config["DATABASE_PATH"] = os.environ.get("DATABASE_PATH", "assignment5.db")

    db.init_app(app)
    sock = Sock(app)

    @app.get("/healthz")
    def healthcheck():
        return jsonify({"status": "ok", "database_path": app.config["DATABASE_PATH"]})

    @app.get("/")
    @require_dashboard_auth
    def dashboard():
        session["dashboard_authenticated"] = True
        return render_template("dashboard.html")

    @app.get("/api/todos")
    @require_dashboard_auth
    def list_todos():
        return jsonify(
            {
                "items": db.fetch_todos(),
                "status": "ok",
            }
        )

    @app.post("/api/todos")
    @require_dashboard_auth
    def create_todo():
        payload = request.get_json(silent=True) or {}
        title = (payload.get("title") or "").strip()
        if not title:
            return jsonify({"status": "error", "message": "title is required"}), 400

        item = db.insert_todo(title)
        return jsonify({"status": "created", "item": item}), 201

    @app.post("/api/todos/<int:todo_id>/complete")
    @require_dashboard_auth
    def complete_todo(todo_id):
        item = db.mark_todo_complete(todo_id)
        if item is None:
            return jsonify({"status": "error", "message": "todo not found"}), 404
        return jsonify({"status": "ok", "item": item})

    @app.post("/api/todos/<int:todo_id>/edit")
    @require_dashboard_auth
    def edit_todo(todo_id):
        payload = request.get_json(silent=True) or {}
        title = (payload.get("title") or "").strip()
        if not title:
            return jsonify({"status": "error", "message": "title is required"}), 400

        item = db.update_todo_title(todo_id, title)
        if item is None:
            return jsonify({"status": "error", "message": "todo not found"}), 404
        return jsonify({"status": "ok", "item": item})

    @app.post("/api/todos/<int:todo_id>/delete")
    @require_dashboard_auth
    def remove_todo(todo_id):
        item = db.delete_todo(todo_id)
        if item is None:
            return jsonify({"status": "error", "message": "todo not found"}), 404
        return jsonify({"status": "deleted", "item": item})

    @app.get("/api/notes")
    @require_dashboard_auth
    def list_notes():
        return jsonify(
            {
                "items": db.fetch_notes(),
                "status": "ok",
            }
        )

    @app.post("/api/notes")
    @require_dashboard_auth
    def create_note():
        payload = request.get_json(silent=True) or {}
        transcript = (payload.get("transcript") or "").strip()
        if not transcript:
            return jsonify({"status": "error", "message": "transcript is required"}), 400

        note_analysis = analyze_voice_note(transcript)
        summary = (payload.get("summary") or "").strip() or note_analysis["summary"]
        created_todo = None
        if note_analysis["todo_title"]:
            created_todo = db.insert_todo(note_analysis["todo_title"])

        item = db.insert_note(
            transcript=transcript,
            summary=summary,
            audio_path=(payload.get("audio_path") or "").strip() or None,
            source=(payload.get("source") or "device").strip() or "device",
        )
        return jsonify({"status": "created", "item": item, "created_todo": created_todo}), 201

    @app.post("/api/audio")
    @require_dashboard_auth
    def upload_audio():
        try:
            audio_bytes = read_uploaded_audio(request.files.get("audio"))
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

        result = process_audio_note(audio_bytes, source="dashboard-upload")
        return (
            jsonify(
                {
                    "status": "created",
                    "item": result["note"],
                    "created_todo": result["created_todo"],
                }
            ),
            201,
        )

    @app.get("/api/notes/<int:note_id>/audio")
    @require_dashboard_auth
    def get_note_audio(note_id):
        note = db.fetch_note(note_id)
        if note is None:
            return jsonify({"status": "error", "message": "note not found"}), 404
        audio_path = note.get("audio_path")
        if not audio_path:
            return jsonify({"status": "error", "message": "note has no audio"}), 404

        full_path = resolve_audio_path(audio_path)
        if not os.path.exists(full_path):
            return jsonify({"status": "error", "message": "audio file missing"}), 404
        return send_file(full_path, mimetype="audio/wav", conditional=True)

    @app.get("/api/interactions")
    @require_dashboard_auth
    def list_interactions():
        return jsonify({"items": db.fetch_interactions(), "status": "ok"})

    @app.get("/api/device/state")
    def device_state():
        if not device_or_dashboard_auth_is_valid():
            return device_unauthorized()

        todos = db.fetch_todos(limit=5, include_completed=False)
        notes = db.fetch_notes(limit=1)
        return jsonify(
            {
                "mode": "todo",
                "todo_preview": todos,
                "last_note": notes[0] if notes else None,
                "status": "ok",
            }
        )

    @sock.route("/ws/assistant")
    def assistant_socket(ws):
        if not device_api_key_is_valid():
            ws.send("R:Unauthorized device.")
            ws.send("D")
            return

        interaction = db.insert_interaction(status="connected")
        audio_buffer = bytearray()
        recording = False

        while True:
            message = ws.receive()
            if message is None:
                db.update_interaction(interaction["id"], status="closed")
                break

            if isinstance(message, str):
                if message == "start":
                    audio_buffer.clear()
                    recording = True
                    db.update_interaction(interaction["id"], status="recording")

                elif message == "stop":
                    recording = False
                    if len(audio_buffer) < MIN_AUDIO_BYTES:
                        db.update_interaction(interaction["id"], status="too_short")
                        ws.send("T:(too short)")
                        ws.send("R:Hold the button a little longer and try again.")
                        ws.send("D")
                        continue

                    try:
                        processed = process_audio_note(bytes(audio_buffer), source="device")
                        transcript = processed["transcript"]
                        created_todo = processed["created_todo"]
                    except Exception as exc:
                        transcript = f"(transcription error: {exc})"
                        created_todo = None

                    ws.send(f"T:{transcript}")

                    try:
                        assistant_response = generate_assistant_response(transcript, created_todo)
                        status = "completed"
                    except Exception as exc:
                        assistant_response = f"Error generating response: {exc}"
                        status = "error"

                    db.update_interaction(
                        interaction["id"],
                        transcript=transcript,
                        assistant_response=assistant_response,
                        status=status,
                    )

                    ws.send(f"R:{assistant_response}")
                    ws.send("D")

                    interaction = db.insert_interaction(status="connected")

            elif isinstance(message, bytes) and recording:
                audio_buffer.extend(message)

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host="0.0.0.0", port=port, debug=debug)
