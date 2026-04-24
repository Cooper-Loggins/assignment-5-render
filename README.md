# ELEE 2045 Assignment 5: Smart Voice Assistant & Cloud Web Dashboard

**Name:** Cooper Loggins  

<img src="static/images/headshot.jpg" alt="Cooper Loggins Headshot" width="200"/>

---

## 🌐 Live Deployment Link
**Dashboard URL:** https://assignment-5-dashboard.onrender.com/
> *Note: This deployment is intended to stay active for grading.*

---

## 🎥 Video Demonstration
Link to Assignment 5 Video: Add your final YouTube link here
> *Note: The video link is the only remaining placeholder in this README. Everything else below reflects the current implementation in this repository.*

---

## 🚀 Project Description
> This project is a smart voice assistant built around an M5StickC Plus 2, a Flask backend, and a cloud-hosted web dashboard. The wearable device connects to Wi-Fi, opens a secure WebSocket to the backend, records microphone audio, and streams raw 16 kHz PCM audio to the server during a live assistant session. After the server finishes processing the recording, it returns a transcript and a short assistant reply that fit on the small M5 display. The firmware also polls a compact HTTPS endpoint so the device can show a to-do preview mode and allow the user to cycle through tasks or mark them complete directly from the wearable.
>
> On the backend, Flask serves both the HTML dashboard and the JSON API routes, while Flask-Sock handles the live WebSocket assistant connection. The application stores all persistent data in SQLite. There are three main tables: `todos`, `notes`, and `interactions`. `todos` stores task titles and completion state, `notes` stores the original transcript, summary, source, and optional saved audio path, and `interactions` stores the transcript/assistant-response history for the live assistant channel.
>
> For speech processing, the server sends the uploaded raw PCM stream to Wit.ai when `WIT_TOKEN` is configured. After transcription, the backend stores the note and optionally runs an additional GenAI analysis step using `google-genai` with the `gemini-3.1-flash-lite-preview` model. That step creates a concise summary for the dashboard and may extract actionable to-do items from explicit task language in the transcript. The dashboard itself is a single-page Flask-rendered interface that lets the user add, edit, complete, delete, and clear to-dos; create and delete notes; upload WAV files for transcription; play back saved note audio; inspect recent interaction history; and view the compact device state returned to the M5.

---

## ☁️ Cloud Deployment & Security Architecture
> The project is deployed as a single Python web service on Render using `gunicorn` as the production server. The included `render.yaml` config provisions the web service, sets the health check path to `/healthz`, installs dependencies from `requirements.txt`, and starts the Flask app with a threaded Gunicorn worker. The same deployed application serves the dashboard HTML, all JSON APIs, the note audio playback route, and the `/ws/assistant` secure WebSocket endpoint used by the M5Stick.
>
> Persistent storage is handled with SQLite on a mounted Render disk. In production, `DATABASE_PATH` points to `/opt/render/project/src/data/assignment5.db`, which allows notes, to-dos, audio metadata, and interaction history to survive deploys and restarts. The app also stores note audio files in a `media/audio` folder rooted beside the active database path, so audio uploads and device recordings stay associated with the persistent storage location.
>
> Security is split by client type:
>
> 1. The browser dashboard is protected with HTTP Basic Auth. The expected credentials come from `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`.
> 2. Device-facing routes use a separate `DEVICE_API_KEY`. The M5 sends that key through the `X-Device-API-Key` header for HTTPS requests and through the `api_key` query parameter for the secure WebSocket connection.
> 3. The `device/state` route accepts either device auth or dashboard auth because the browser dashboard also needs to display the same compact state snapshot shown on the M5.
> 4. The `/healthz` route is intentionally left open so deployment verification and Render health checks can succeed without requiring authentication.
>
> This is effectively a monolithic cloud deployment: frontend, backend, auth, persistence, and WebSocket handling all live in one Flask application. That approach kept the project simpler to debug while still satisfying the requirement for a public cloud-hosted dashboard plus a networked embedded client.

---

## 🛠️ How to Build & Run Locally (For Development)
> The local workflow uses a Python virtual environment, a project-root `.env` file, and the same Flask app entry point used in production. The server will automatically create the SQLite database file on first launch if it does not already exist. For local development, the app defaults to `assignment5.db` in the project directory unless `DATABASE_PATH` is overridden.

**Python Dependencies:**  
`flask`  
`flask-sock`  
`google-genai`  
`gunicorn`

**Installation:**  
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**Environment Variables:**  
> Copy `env.example` to `.env` in the repository root, then fill in the values. The app calls `load_env_file()` automatically on startup, so no extra export step is required if `.env` exists.
>
> Required/important variables:
>
> - `WIT_TOKEN`: Wit.ai token used for speech-to-text transcription of raw PCM audio from the M5 or WAV uploads from the dashboard.
> - `VERTEX_API_KEY`: API key used by the GenAI note summarization and task extraction extension.
> - `APP_SECRET_KEY`: Flask secret key used for the session/auth flow.
> - `DEVICE_API_KEY`: shared key that must match the key compiled into `firmware/firmware.ino`.
> - `DASHBOARD_USERNAME`: HTTP Basic Auth username for the website.
> - `DASHBOARD_PASSWORD`: HTTP Basic Auth password for the website.
> - `DATABASE_PATH`: optional override for the SQLite database file location. If omitted locally, the app uses `assignment5.db`.

**Example local `.env` setup:**  
```env
WIT_TOKEN=your_wit_ai_token
VERTEX_API_KEY=your_vertex_ai_express_api_key
APP_SECRET_KEY=replace_me
DEVICE_API_KEY=replace_me
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=replace_me
DATABASE_PATH=assignment5.db
```

**Run the Flask server locally:**  
```bash
.venv/bin/python app.py
```

**Local URLs:**  

- Dashboard: `http://127.0.0.1:5001/`
- Health check: `http://127.0.0.1:5001/healthz`

**What happens on first startup:**  

1. Flask loads `.env` if present.
2. The SQLite database file and parent folders are created automatically if they do not exist.
3. The schema for `todos`, `notes`, and `interactions` is initialized.
4. The app starts listening on the local port configured inside `app.py`.

**Useful local API endpoints for testing:**  

- `GET /healthz`
- `GET /api/todos`
- `POST /api/todos`
- `POST /api/todos/<id>/complete`
- `POST /api/todos/<id>/edit`
- `POST /api/todos/<id>/delete`
- `POST /api/todos/clear`
- `GET /api/notes`
- `POST /api/notes`
- `POST /api/audio`
- `GET /api/notes/<id>/audio`
- `POST /api/notes/<id>/delete`
- `POST /api/notes/clear`
- `GET /api/interactions`
- `POST /api/interactions/<id>/delete`
- `POST /api/interactions/clear`
- `GET /api/device/state`
- `POST /api/device/todos/<id>/complete`
- `WS /ws/assistant`

**Local development notes:**  

- If `WIT_TOKEN` is missing, transcription does not fail catastrophically, but the server returns a placeholder message indicating that transcription is unavailable.
- If `VERTEX_API_KEY` is missing, the assistant still works, but the GenAI summary/task extraction extension falls back to a shorter local summary path.
- Only WAV uploads are accepted by the dashboard upload route, and uploaded WAV files must be 16-bit mono at 16000 Hz to match the backend expectations.
- The dashboard requires Basic Auth even locally, so use the username/password defined in `.env`.

**M5 firmware setup for local or production testing:**  

1. Open `firmware/wifi_setup/wifi_setup.ino` in the Arduino IDE if Wi-Fi credentials have not yet been stored on the device.
2. Flash the Wi-Fi setup sketch and confirm the device successfully connects.
3. Open `firmware/firmware.ino`.
4. Update these values before flashing:
   - `SERVER_HOST`
   - `DEVICE_STATE_URL`
   - `COMPLETE_TODO_URL_BASE`
   - `DEVICE_API_KEY`
5. Reflash `firmware.ino`.
6. On the live deployment, the firmware uses HTTPS/WSS on port `443`.

**Current M5 controls:**  

- `BtnA` short press from ready state: start recording
- `BtnA` short press while recording: stop recording
- `BtnA` long hold: switch between Todo view and Assistant view
- `BtnB` short press in Todo view: move to the next open todo
- `BtnB` hold in Todo view: mark the selected todo complete
- `BtnB` short press while recording: cancel recording
- `BtnB` short press in Assistant view: scroll the response/transcript display

**Render production configuration included in this repo:**  

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn --bind 0.0.0.0:$PORT --worker-class gthread --workers 1 --threads 100 app:app`
- Health check path: `/healthz`
- Persistent database path: `/opt/render/project/src/data/assignment5.db`

**Recommended manual test flow before demoing:**  

1. Open the public dashboard and sign in.
2. Confirm `/healthz` returns JSON with `"status":"ok"`.
3. Add a todo from the website.
4. Confirm the todo appears on the M5 preview screen.
5. Record a voice note from the M5.
6. Confirm the transcript appears as a saved note on the dashboard.
7. Ask a mixed question+todo utterance and verify the assistant answers the question while also creating the todo item.
8. Mark a todo complete from the M5 and verify the dashboard updates.
9. Upload a WAV file from the website and verify transcription plus audio playback.
10. Test the clear buttons for notes, interactions, and todos.

---

## ✨ Creative Extension
**GenAI Note Summarization and Task Extraction**
> The creative extension in this project is the GenAI-powered understanding layer added on top of the basic voice-note workflow. After a note is transcribed, the backend can send longer transcripts to a GenAI model and request two structured outputs: a concise dashboard-ready summary and an optional actionable to-do title. That summary is stored in the `notes` table so the dashboard and compact device-state view can show a shorter version of the note rather than always displaying the entire transcript.
>
> The more important extension behavior is task extraction. If the note contains clear task language, the backend can convert that spoken content into actual to-do entries stored in SQLite. Those tasks then show up in the dashboard planner as well as in the device's compact to-do preview mode, so the GenAI step is not only decorative; it changes how the user interacts with the note afterward. This extends the assignment beyond basic record/transcribe/display behavior by turning natural speech into structured, actionable state that is synchronized across both the M5Stick and the cloud dashboard.
>
> In the final implementation, the system also supports mixed utterances more intelligently. If the user asks a question and also states an explicit to-do in the same recording, the backend separates those intents so the assistant can answer the question while still saving the task. If the user lists multiple explicit to-dos in one recording, the backend can create multiple todo entries from that single spoken note. This makes the creative extension more useful than a simple one-note/one-task pipeline and demonstrates a more advanced speech-to-structured-action workflow.
