import json
import os
import urllib.request

from flask import Flask, jsonify, render_template, request
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


def maybe_create_todo(transcript):
    lowered = transcript.lower().strip()
    prefixes = [
        "todo ",
        "todo:",
        "add todo ",
        "add a todo ",
        "remember to ",
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


def generate_assistant_response(transcript, created_todo):
    api_key = os.environ.get("VERTEX_API_KEY")
    if not api_key:
        return build_fallback_response(transcript, created_todo)

    prompt = transcript
    if created_todo:
        prompt = (
            f"User said: {transcript}\n"
            f"A todo was already created with title: {created_todo['title']}.\n"
            "Acknowledge that briefly."
        )

    client = genai.Client(vertexai=True, api_key=api_key)
    response = client.models.generate_content(
        model=LLM_MODEL,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        ),
    )
    text = (response.text or "").strip()
    return text or build_fallback_response(transcript, created_todo)


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
    def dashboard():
        return render_template("dashboard.html")

    @app.get("/api/todos")
    def list_todos():
        return jsonify(
            {
                "items": db.fetch_todos(),
                "status": "ok",
            }
        )

    @app.post("/api/todos")
    def create_todo():
        payload = request.get_json(silent=True) or {}
        title = (payload.get("title") or "").strip()
        if not title:
            return jsonify({"status": "error", "message": "title is required"}), 400

        item = db.insert_todo(title)
        return jsonify({"status": "created", "item": item}), 201

    @app.post("/api/todos/<int:todo_id>/complete")
    def complete_todo(todo_id):
        item = db.mark_todo_complete(todo_id)
        if item is None:
            return jsonify({"status": "error", "message": "todo not found"}), 404
        return jsonify({"status": "ok", "item": item})

    @app.get("/api/notes")
    def list_notes():
        return jsonify(
            {
                "items": db.fetch_notes(),
                "status": "ok",
            }
        )

    @app.post("/api/notes")
    def create_note():
        payload = request.get_json(silent=True) or {}
        transcript = (payload.get("transcript") or "").strip()
        if not transcript:
            return jsonify({"status": "error", "message": "transcript is required"}), 400

        item = db.insert_note(
            transcript=transcript,
            summary=(payload.get("summary") or "").strip() or None,
            audio_path=(payload.get("audio_path") or "").strip() or None,
            source=(payload.get("source") or "device").strip() or "device",
        )
        return jsonify({"status": "created", "item": item}), 201

    @app.get("/api/interactions")
    def list_interactions():
        return jsonify({"items": db.fetch_interactions(), "status": "ok"})

    @app.get("/api/device/state")
    def device_state():
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
                        transcript = transcribe_audio(bytes(audio_buffer))
                    except Exception as exc:
                        transcript = f"(transcription error: {exc})"

                    ws.send(f"T:{transcript}")

                    summary = local_summary(transcript)
                    db.insert_note(
                        transcript=transcript,
                        summary=summary,
                        source="device",
                    )
                    created_todo = maybe_create_todo(transcript)

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
    app.run(host="0.0.0.0", port=5000, debug=True)
