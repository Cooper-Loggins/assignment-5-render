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

## Creative extension

- Longer voice notes are analyzed with the LLM before storage
- The backend generates a concise summary for the dashboard and device view
- When a note clearly implies an action, the backend creates a short actionable to-do automatically
- This turns voice notes into dashboard-ready tasks without requiring strict spoken prefixes

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
- `/api/device/state` also allows dashboard Basic Auth so the browser UI can load the device snapshot
- Health check remains open for simple deployment verification

## Local development

Install dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run the Flask app:

```bash
.venv/bin/python app.py
```

The app auto-loads environment variables from `.env` if that file exists.

Default local URLs:

- Dashboard: `http://127.0.0.1:5001/`
- Health check: `http://127.0.0.1:5001/healthz`

The SQLite database is created automatically on startup at `DATABASE_PATH`.

## Production deployment for grading

This project can be deployed to Render as a public web service so it stays online
without your laptop running.

Important deployment notes:

- Render web services support WebSockets, which this app needs for `WS /ws/assistant`.
- Render expects the app to bind to `0.0.0.0` on the `PORT` environment variable.
- SQLite requires persistent storage in production. On Render, that means attaching a
  persistent disk and pointing `DATABASE_PATH` into that mounted directory.
- Render persistent disks are available only on paid web services. If you stay on
  Render free, the filesystem is ephemeral and your SQLite data will be lost on
  restart or redeploy.

This repo includes [render.yaml](/Users/cooperloggins/Desktop/assignment-5-Cooper-Loggins/render.yaml:1)
to preconfigure the service.

### Render setup

1. Push this repo to GitHub.
2. In Render, create a new Blueprint or Web Service from that repo.
3. If using the included `render.yaml`, let Render create the `assignment-5-dashboard`
   service.
4. In the Render dashboard, fill in these secret environment variables:
   - `WIT_TOKEN`
   - `VERTEX_API_KEY`
   - `DEVICE_API_KEY`
   - `DASHBOARD_USERNAME`
   - `DASHBOARD_PASSWORD`
5. Deploy the service.
6. Confirm the health check works at `/healthz`.

The included configuration uses:

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn --bind 0.0.0.0:$PORT --threads 8 app:app`
- Health check path: `/healthz`
- Persistent SQLite path: `/opt/render/project/src/data/assignment5.db`

### After deploy

Once Render gives you a permanent hostname such as `your-service.onrender.com`:

1. Update `SERVER_HOST` in `firmware/firmware.ino`
2. Update `DEVICE_STATE_URL` in `firmware/firmware.ino`
3. Reflash the M5 with the deployed host

Use:

- `https://your-service.onrender.com/api/device/state`
- `wss://your-service.onrender.com/ws/assistant`

Your M5 firmware keeps using port `443`, so only the hostname changes.
