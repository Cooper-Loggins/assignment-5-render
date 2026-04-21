import os

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("APP_SECRET_KEY", "dev-secret")
    app.config["DATABASE_PATH"] = os.environ.get("DATABASE_PATH", "assignment5.db")

    sock = Sock(app)

    @app.get("/healthz")
    def healthcheck():
        return jsonify({"status": "ok"})

    @app.get("/")
    def dashboard():
        return render_template("dashboard.html")

    @app.get("/api/todos")
    def list_todos():
        return jsonify(
            {
                "items": [],
                "status": "placeholder",
                "message": "Todo persistence will be added in the next step.",
            }
        )

    @app.post("/api/todos")
    def create_todo():
        payload = request.get_json(silent=True) or {}
        return (
            jsonify(
                {
                    "status": "placeholder",
                    "received": payload,
                    "message": "Todo creation will be implemented after the database layer exists.",
                }
            ),
            501,
        )

    @app.post("/api/todos/<int:todo_id>/complete")
    def complete_todo(todo_id):
        return (
            jsonify(
                {
                    "status": "placeholder",
                    "todo_id": todo_id,
                    "message": "Completion workflow will be implemented after the database layer exists.",
                }
            ),
            501,
        )

    @app.get("/api/notes")
    def list_notes():
        return jsonify(
            {
                "items": [],
                "status": "placeholder",
                "message": "Note persistence will be added in the next step.",
            }
        )

    @app.get("/api/device/state")
    def device_state():
        return jsonify(
            {
                "mode": "todo",
                "todo_preview": [],
                "last_note": None,
                "status": "placeholder",
            }
        )

    @sock.route("/ws/assistant")
    def assistant_socket(ws):
        ws.send("STATUS:placeholder")
        ws.send("MESSAGE:Assistant streaming will be connected in a later step.")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
