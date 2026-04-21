import os

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock

import db


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
        ws.send("STATUS:placeholder")
        ws.send(f"MESSAGE:Assistant streaming will be connected in a later step. session={interaction['id']}")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
