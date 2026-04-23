# ELEE 2045 Assignment 5

**Name:** Cooper Loggins

This project is a smart voice assistant built around an M5StickC Plus 2 and a
publicly deployed Flask dashboard. The device records voice notes, streams audio
to the cloud backend, receives compact to-do state from the server, and lets the
user review or complete tasks directly from the wearable screen. The web app
stores notes, audio, to-dos, and interaction history in SQLite, then presents them
through a styled dashboard that supports playback, manual editing, cleanup, and
device sync.

**Video Demo:** Add your final YouTube link here.

## Project overview

Main implementation areas:

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

## Live deployment

- Public dashboard: `https://assignment-5-dashboard.onrender.com/`
- Health check: `https://assignment-5-dashboard.onrender.com/healthz`
- Dashboard username: `Cooperlee7`
- Dashboard password: `Cooperlee7`

Current website features:

- view saved notes and recent assistant history
- play back saved note audio in the browser
- add, edit, complete, and delete to-do items
- clear saved notes and interaction history from the dashboard
- upload WAV audio directly for transcription and note storage

Current M5Stick controls:

- `Todo Mode`
- `A` short press: start recording
- `A` long hold: switch between Todo and Assistant views
- `B` short press: move to the next to-do item
- `B` short hold: mark the selected to-do item done

- `Assistant view`
- `A` short press: start recording
- `A` while recording: stop recording
- after a response returns, the display updates with transcript and reply text

Current creative extension:

- the backend uses the LLM to summarize longer notes automatically
- if a note implies a task, the backend creates a short actionable to-do automatically

Suggested demo flow:

1. Open the public dashboard and sign in.
2. Confirm `/healthz` returns `{"status":"ok", ...}`.
3. Record a voice note on the M5.
4. Show the new note on the dashboard.
5. Play the saved audio from the note entry.
6. Cycle through to-dos on the M5 with `B`.
7. Mark a selected to-do item done from the M5 with a short hold on `B`.
8. Show the updated to-do state on the dashboard.

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
- Start command: `gunicorn --bind 0.0.0.0:$PORT --worker-class gthread --workers 1 --threads 100 app:app`
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
