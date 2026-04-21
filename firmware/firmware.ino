/*
 * M5StickC Plus 2 firmware for Assignment 5
 *
 * Responsibilities:
 * - connect to Wi-Fi from stored Preferences
 * - stream microphone audio to the Flask WebSocket assistant
 * - poll compact device state from the Flask dashboard API
 * - show both a to-do preview mode and live voice interaction feedback
 *
 * Arduino IDE setup:
 * - Board manager URL:
 *   https://static-cdn.m5stack.com/resource/arduino/package_m5stack_index.json
 * - Board: M5StickCPlus2 (m5stack:esp32)
 * - Libraries: M5Unified, WebSockets by Markus Sattler
 */

#include <HTTPClient.h>
#include <M5Unified.h>
#include <Preferences.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#define SERVER_HOST "your-public-hostname.example.com"
#define SERVER_PORT 443
#define WS_PATH "/ws/assistant"
#define DEVICE_STATE_URL "https://your-public-hostname.example.com/api/device/state"
#define DEVICE_API_KEY "replace_me"

#define SAMPLE_RATE 16000
#define MIC_BUF_LEN 256
#define STATE_REFRESH_MS 15000
#define MAX_TODO_ITEMS 5

WebSocketsClient ws;
Preferences prefs;
String wsExtraHeaders;

enum AssistantState { DISCONNECTED, READY, RECORDING, PROCESSING };
AssistantState assistantState = DISCONNECTED;

enum ScreenMode { MODE_TODOS, MODE_RESPONSE };
ScreenMode screenMode = MODE_TODOS;

String todoItems[MAX_TODO_ITEMS];
int todoCount = 0;
String lastNoteSummary = "No notes yet";
String lastTranscript = "";
String lastResponse = "Press A to record";
String deviceMode = "todo";

bool firstResponseChunk = true;
unsigned long lastStateRefresh = 0;

String decodeJsonString(String value) {
  value.replace("\\n", "\n");
  value.replace("\\\"", "\"");
  value.replace("\\/", "/");
  return value;
}

String extractFirstJsonString(const String &json, const String &key) {
  String pattern = "\"" + key + "\":\"";
  int start = json.indexOf(pattern);
  if (start < 0) {
    return "";
  }

  start += pattern.length();
  String result = "";
  bool escaped = false;

  for (int i = start; i < json.length(); i++) {
    char c = json[i];
    if (escaped) {
      result += c;
      escaped = false;
      continue;
    }
    if (c == '\\') {
      escaped = true;
      result += c;
      continue;
    }
    if (c == '"') {
      break;
    }
    result += c;
  }

  return decodeJsonString(result);
}

int extractTodoTitles(const String &json) {
  int count = 0;
  int searchStart = 0;
  String pattern = "\"title\":\"";

  while (count < MAX_TODO_ITEMS) {
    int titlePos = json.indexOf(pattern, searchStart);
    if (titlePos < 0) {
      break;
    }

    titlePos += pattern.length();
    String result = "";
    bool escaped = false;

    for (int i = titlePos; i < json.length(); i++) {
      char c = json[i];
      if (escaped) {
        result += c;
        escaped = false;
        continue;
      }
      if (c == '\\') {
        escaped = true;
        result += c;
        continue;
      }
      if (c == '"') {
        searchStart = i + 1;
        break;
      }
      result += c;
    }

    todoItems[count] = decodeJsonString(result);
    count++;
  }

  return count;
}

bool connectWiFi() {
  prefs.begin("wifi", true);
  uint8_t count = prefs.getUChar("count", 0);

  for (uint8_t i = 0; i < count; i++) {
    char key[8];
    snprintf(key, sizeof(key), "ssid%d", i);
    String ssid = prefs.getString(key, "");
    snprintf(key, sizeof(key), "user%d", i);
    String user = prefs.getString(key, "");
    snprintf(key, sizeof(key), "pass%d", i);
    String pass = prefs.getString(key, "");

    if (ssid.isEmpty()) {
      continue;
    }

    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    if (user.length() > 0) {
      WiFi.begin(ssid.c_str(), WPA2_AUTH_PEAP, user.c_str(), user.c_str(), pass.c_str());
    } else {
      WiFi.begin(ssid.c_str(), pass.c_str());
    }

    for (int t = 0; t < 12 && WiFi.status() != WL_CONNECTED; t++) {
      delay(1000);
    }

    if (WiFi.status() == WL_CONNECTED) {
      prefs.end();
      return true;
    }
  }

  prefs.end();
  return false;
}

void drawHeader(const char *label, uint16_t color) {
  M5.Display.fillRect(0, 0, 240, 18, BLACK);
  M5.Display.setCursor(4, 2);
  M5.Display.setTextSize(2);
  M5.Display.setTextColor(color, BLACK);
  M5.Display.print(label);
}

void clearBody() {
  M5.Display.fillRect(0, 20, 240, 115, BLACK);
  M5.Display.setCursor(4, 24);
  M5.Display.setTextSize(1);
  M5.Display.setTextColor(WHITE, BLACK);
}

void renderTodoMode() {
  drawHeader("Todo Mode", GREEN);
  clearBody();
  M5.Display.println("B: switch view");
  M5.Display.println("A: record note");
  M5.Display.println();

  if (todoCount == 0) {
    M5.Display.println("No open todos");
  } else {
    for (int i = 0; i < todoCount; i++) {
      M5.Display.printf("%d. %s\n", i + 1, todoItems[i].c_str());
    }
  }

  M5.Display.println();
  M5.Display.print("Last note: ");
  M5.Display.println(lastNoteSummary);
}

void renderResponseMode() {
  uint16_t color = CYAN;
  const char *label = "Assistant";

  if (assistantState == RECORDING) {
    color = RED;
    label = "Recording";
  } else if (assistantState == PROCESSING) {
    color = YELLOW;
    label = "Thinking";
  } else if (assistantState == DISCONNECTED) {
    color = RED;
    label = "Offline";
  }

  drawHeader(label, color);
  clearBody();
  M5.Display.println("B: switch view");
  M5.Display.println("A: record note");
  M5.Display.println();
  M5.Display.print("You: ");
  M5.Display.println(lastTranscript);
  M5.Display.println();
  M5.Display.print("Reply: ");
  M5.Display.println(lastResponse);
}

void renderScreen() {
  if (screenMode == MODE_TODOS && assistantState != RECORDING && assistantState != PROCESSING) {
    renderTodoMode();
  } else {
    renderResponseMode();
  }
}

void fetchDeviceState() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;

  if (!http.begin(client, DEVICE_STATE_URL)) {
    lastResponse = "State fetch failed";
    return;
  }

  http.addHeader("X-Device-API-Key", DEVICE_API_KEY);
  int code = http.GET();
  if (code <= 0) {
    http.end();
    lastResponse = "State GET error";
    return;
  }

  String payload = http.getString();
  http.end();

  deviceMode = extractFirstJsonString(payload, "mode");
  String summary = extractFirstJsonString(payload, "summary");
  String transcript = extractFirstJsonString(payload, "transcript");

  if (summary.length() > 0) {
    lastNoteSummary = summary;
  } else if (transcript.length() > 0) {
    lastNoteSummary = transcript;
  } else {
    lastNoteSummary = "No notes yet";
  }

  for (int i = 0; i < MAX_TODO_ITEMS; i++) {
    todoItems[i] = "";
  }
  todoCount = extractTodoTitles(payload);
}

void onWebSocket(WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      assistantState = DISCONNECTED;
      renderScreen();
      break;

    case WStype_CONNECTED:
      assistantState = READY;
      lastResponse = "Ready for notes";
      renderScreen();
      break;

    case WStype_TEXT: {
      String msg((char *)payload);

      if (msg.startsWith("T:")) {
        assistantState = PROCESSING;
        lastTranscript = msg.substring(2);
        lastResponse = "";
        firstResponseChunk = true;
      } else if (msg.startsWith("R:")) {
        if (firstResponseChunk) {
          lastResponse = "";
          firstResponseChunk = false;
        }
        lastResponse += msg.substring(2);
      } else if (msg == "D") {
        assistantState = READY;
        fetchDeviceState();
      }

      renderScreen();
      break;
    }

    default:
      break;
  }
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(1);
  M5.Display.fillScreen(BLACK);

  drawHeader("WiFi...", YELLOW);
  clearBody();
  M5.Display.println("Connecting to Wi-Fi");

  if (!connectWiFi()) {
    drawHeader("No WiFi", RED);
    clearBody();
    M5.Display.println("Check stored prefs");
    while (true) {
      delay(1000);
    }
  }

  M5.Speaker.end();
  M5.Mic.begin();

  ws.beginSSL(SERVER_HOST, SERVER_PORT, WS_PATH);
  wsExtraHeaders = "X-Device-API-Key: " + String(DEVICE_API_KEY) + "\r\n";
  ws.setExtraHeaders(wsExtraHeaders.c_str());
  ws.onEvent(onWebSocket);
  ws.setReconnectInterval(3000);

  fetchDeviceState();
  renderScreen();
}

void loop() {
  M5.update();
  ws.loop();

  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  if (millis() - lastStateRefresh > STATE_REFRESH_MS && assistantState != RECORDING) {
    lastStateRefresh = millis();
    fetchDeviceState();
    renderScreen();
  }

  if (M5.BtnB.wasPressed()) {
    screenMode = (screenMode == MODE_TODOS) ? MODE_RESPONSE : MODE_TODOS;
    renderScreen();
  }

  if (M5.BtnA.wasPressed()) {
    if (assistantState == READY) {
      assistantState = RECORDING;
      screenMode = MODE_RESPONSE;
      firstResponseChunk = true;
      lastTranscript = "";
      lastResponse = "Listening...";
      ws.sendTXT("start");
      renderScreen();
    } else if (assistantState == RECORDING) {
      assistantState = PROCESSING;
      ws.sendTXT("stop");
      renderScreen();
    }
  }

  if (assistantState == RECORDING) {
    int16_t buffer[MIC_BUF_LEN];
    if (M5.Mic.record(buffer, MIC_BUF_LEN, SAMPLE_RATE)) {
      ws.sendBIN((uint8_t *)buffer, sizeof(buffer));
    }
  }
}
