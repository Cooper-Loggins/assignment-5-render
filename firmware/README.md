# Firmware Plan

The final firmware will be moved into this directory after the backend contract is stable.

Current planned responsibilities:

- connect to Wi-Fi using stored preferences
- open a secure WebSocket to `/ws/assistant`
- stream microphone audio while recording
- fetch compact todo state from `/api/device/state`
- send device API key during backend communication
