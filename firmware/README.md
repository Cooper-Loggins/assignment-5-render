# Firmware

`firmware.ino` is the current Assignment 5 sketch for the M5StickC Plus 2.

Current behavior:

- connect to Wi-Fi using stored Preferences credentials
- open a secure WebSocket to `/ws/assistant`
- stream microphone audio while recording
- poll `/api/device/state` over HTTPS
- show a to-do preview mode
- show a live assistant response mode
- send `X-Device-API-Key` for both HTTPS and WSS requests

Current controls:

- `BtnA`: start/stop note recording
- `BtnB`: toggle between to-do view and assistant view

Before flashing:

- update `SERVER_HOST`
- update `DEVICE_STATE_URL`
- update `DEVICE_API_KEY` to match the server `.env`
- make sure your public deployment is running over HTTPS / WSS
