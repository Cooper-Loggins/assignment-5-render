# Assignment 5 Architecture

## Selected baseline

The project will be built from the voice-assistant prototype direction rather than the older sensor dashboard direction.

- Keep: audio streaming, STT, assistant response workflow
- Remove: sensor/noise monitoring dashboard model

## Planned components

### Embedded client

- Record microphone audio on the M5StickC Plus 2
- Stream raw PCM audio to the backend over secure WebSocket
- Show short device-friendly responses
- Fetch compact state for to-do display mode

### Python backend

- Flask app for dashboard and JSON APIs
- Flask-Sock WebSocket endpoint for live assistant interaction
- SQLite for notes, todos, and interaction history
- STT provider for transcription
- LLM provider for note summarization and assistant behavior

### Dashboard

- Show notes and transcriptions
- Show and manage todos
- Show recent interactions and device-facing state

### Security

- Device API key for embedded access
- Dashboard authentication for web access

## Near-term implementation order

1. Create repo scaffold and route layout
2. Add SQLite schema and data access helpers
3. Wire routes to persistence
4. Replace placeholder dashboard with functional UI
5. Integrate firmware with final routes and auth
