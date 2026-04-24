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
> *Note: The demo should stay under 4 minutes and show the M5Stick and dashboard syncing in both directions.*

---

## 🚀 Project Description
> This project is a smart voice assistant built with an M5StickC Plus 2, a Flask backend, and a hosted web dashboard. The device records microphone audio, streams raw PCM audio to the server over a WebSocket connection, and receives short assistant responses plus compact to-do state that fits on the wearable screen. On the backend, Flask handles the HTTP routes and Flask-Sock manages the live audio WebSocket. The app stores notes, summaries, audio file paths, to-dos, and assistant interaction history in SQLite. Transcription is handled with Wit.ai when `WIT_TOKEN` is configured, and longer voice notes are optionally analyzed with Google's GenAI client using the `gemini-3.1-flash-lite-preview` model to generate concise summaries and possible task extraction. The dashboard presents notes, audio playback, to-dos, and recent assistant interactions in one place for browser-based management.

---

## ☁️ Cloud Deployment & Security Architecture
> The project is deployed as a single Python web service on Render. The same Flask app serves the dashboard HTML, JSON API routes, and the WebSocket endpoint used by the M5Stick. SQLite is stored on a mounted Render persistent disk so note and to-do data survive redeploys. Security is split by client type. Browser dashboard routes and dashboard JSON APIs are protected with HTTP Basic Auth using `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`. Device-facing endpoints require an `X-Device-API-Key` header or the WebSocket `api_key` query parameter, backed by the `DEVICE_API_KEY` environment variable. The public `/healthz` route is intentionally left open so the deployment can be validated quickly without logging in.

---

## 🛠️ How to Build & Run Locally (For Development)
> Create a Python virtual environment, install the dependencies from `requirements.txt`, and place your secrets in a local `.env` file in the project root. The Flask app auto-loads that file on startup. You will need values for Wit.ai transcription, the GenAI API key if you want the creative extension active, dashboard login credentials, and a device API key for the M5Stick.

**Installation:**  
`python3 -m venv .venv`  
`.venv/bin/pip install -r requirements.txt`

**Environment Variables:**  
> Copy `env.example` to `.env` and fill in:
> `WIT_TOKEN`, `VERTEX_API_KEY`, `APP_SECRET_KEY`, `DEVICE_API_KEY`, `DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`, and optionally `DATABASE_PATH`.

**Execution:**  
`.venv/bin/python app.py`

Local URLs:

- Dashboard: `http://127.0.0.1:5001/`
- Health check: `http://127.0.0.1:5001/healthz`

---

## ✨ Creative Extension
**GenAI Note Summarization and Task Extraction**
> The creative extension is the automatic understanding of longer voice notes. After transcription, the backend analyzes notes that are long enough to contain real context and asks a GenAI model to return a short dashboard-ready summary plus an optional actionable to-do title. If the note clearly implies a task, the server automatically inserts that to-do into SQLite so it appears on both the dashboard and the M5Stick's to-do preview screen. This extends the base assignment beyond simple recording and playback by turning natural speech into structured, usable task data.
