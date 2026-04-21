# ELEE 2045 Assignment 5

This repository contains the Assignment 5 implementation for the smart voice assistant and cloud web dashboard project.

## Step-by-step build plan

1. Scaffold the Assignment 5 project structure and backend route layout.
2. Add the SQLite schema and persistence layer for notes, todos, and interaction history.
3. Implement the Flask API routes and WebSocket assistant workflow against the database.
4. Build the dashboard UI for notes, todos, and device state.
5. Connect the M5Stick firmware to the finalized backend workflow.
6. Add authentication for the dashboard and device API access.
7. Implement the creative extension.
8. Finalize deployment and submission materials.

## Planned backend surface

- `GET /` dashboard
- `GET /healthz` health check
- `GET /api/todos` fetch todo list
- `POST /api/todos` create todo
- `POST /api/todos/<id>/complete` mark todo complete
- `GET /api/notes` fetch saved notes
- `POST /api/notes` create note
- `GET /api/interactions` fetch interaction history
- `GET /api/device/state` fetch compact device-friendly state
- `WS /ws/assistant` stream audio, transcription, and assistant responses

## Current assistant behavior

- Accepts the M5Stick-compatible WebSocket control flow with `start`, audio bytes, and `stop`
- Buffers streamed PCM audio on the server
- Transcribes with Wit.ai when `WIT_TOKEN` is configured
- Stores transcripts as notes in SQLite
- Creates todos from simple voice prefixes such as `todo:` or `remember to`
- Saves interaction history and returns a short assistant response

## Current dashboard behavior

- Loads health, todos, notes, interactions, and device state from the live API
- Allows manual todo creation
- Allows marking todos complete
- Allows manual note creation
- Shows recent assistant sessions and the compact device snapshot

## Current firmware behavior

- Streams audio to `WS /ws/assistant`
- Polls `GET /api/device/state` for the compact to-do preview
- Shows a to-do screen and an assistant-response screen on the M5Stick
- Sends `X-Device-API-Key` to the device-facing backend endpoints

## Current auth behavior

- Dashboard routes and dashboard JSON APIs require HTTP Basic Auth
- Device endpoints require an `X-Device-API-Key` header
- Health check remains open for simple deployment verification

## Local development

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the Flask app:

```bash
python app.py
```

The SQLite database is created automatically on startup at `DATABASE_PATH`.
