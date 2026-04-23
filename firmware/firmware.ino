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

#define SERVER_HOST "assignment-5-dashboard.onrender.com"
#define SERVER_PORT 443
#define WS_PATH "/ws/assistant?api_key=" DEVICE_API_KEY
#define DEVICE_STATE_URL "https://assignment-5-dashboard.onrender.com/api/device/state"
#define COMPLETE_TODO_URL_BASE "https://assignment-5-dashboard.onrender.com/api/device/todos/"
// TODO before submission: replace this hard-coded test key with your final device API key.
#define DEVICE_API_KEY "Cooperlee7"

#define SAMPLE_RATE 16000
#define MIC_BUF_LEN 256
#define STATE_REFRESH_MS 60000
#define MAX_TODO_ITEMS 5
#define NOISE_GATE_LEVEL 180
#define MAX_SILENCE_FRAMES 6

WebSocketsClient ws;
Preferences prefs;
String wsExtraHeaders;

enum AssistantState { DISCONNECTED, READY, RECORDING, PROCESSING };
AssistantState assistantState = DISCONNECTED;

enum ScreenMode { MODE_TODOS, MODE_RESPONSE };
ScreenMode screenMode = MODE_TODOS;

String todoItems[MAX_TODO_ITEMS];
int todoIds[MAX_TODO_ITEMS];
int todoCount = 0;
int selectedTodoIndex = 0;
String lastNoteSummary = "No notes yet";
String lastTranscript = "";
String lastResponse = "Press A to record";
String deviceMode = "todo";

bool firstResponseChunk = true;
unsigned long lastStateRefresh = 0;
bool btnBHoldHandled = false;
bool btnAHoldHandled = false;
float dcEstimate = 0.0f;
float smoothSample = 0.0f;
int silentFrameCount = 0;

const int SCREEN_W = 240;
const int SCREEN_H = 135;
const int HEADER_H = 18;
const int BODY_X = 6;
const int BODY_Y = 22;
const int BODY_W = 228;
const int LINE_H = 11;

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

int extractTodoIds(const String &json) {
  int previewStart = json.indexOf("\"todo_preview\":[");
  if (previewStart < 0) {
    return 0;
  }

  int previewEnd = json.indexOf("\"last_note\"", previewStart);
  if (previewEnd < 0) {
    previewEnd = json.length();
  }

  int count = 0;
  int searchStart = previewStart;
  String pattern = "\"id\":";

  while (count < MAX_TODO_ITEMS) {
    int idPos = json.indexOf(pattern, searchStart);
    if (idPos < 0 || idPos >= previewEnd) {
      break;
    }

    idPos += pattern.length();
    while (idPos < previewEnd && json[idPos] == ' ') {
      idPos++;
    }

    int endPos = idPos;
    while (endPos < previewEnd && isDigit(json[endPos])) {
      endPos++;
    }

    todoIds[count] = json.substring(idPos, endPos).toInt();
    count++;
    searchStart = endPos;
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

String compactText(String text) {
  text.replace("\n", " ");
  text.replace("\r", " ");
  while (text.indexOf("  ") >= 0) {
    text.replace("  ", " ");
  }
  text.trim();
  return text;
}

int drawWrappedText(const String &rawText, int x, int y, int maxWidth, int maxLines) {
  String text = compactText(rawText);
  if (text.isEmpty()) {
    return y;
  }

  const int maxChars = maxWidth / 6;
  int start = 0;
  int line = 0;

  while (start < text.length() && line < maxLines) {
    int remaining = text.length() - start;
    int take = remaining;

    if (take > maxChars) {
      take = maxChars;
      int split = text.lastIndexOf(' ', start + take - 1);
      if (split > start) {
        take = split - start;
      }
    }

    String part = text.substring(start, start + take);
    part.trim();
    start += take;

    while (start < text.length() && text[start] == ' ') {
      start++;
    }

    if (line == maxLines - 1 && start < text.length() && part.length() > 3) {
      part = part.substring(0, part.length() - 3) + "...";
    }

    M5.Display.setCursor(x, y + (line * LINE_H));
    M5.Display.println(part);
    line++;
  }

  return y + (line * LINE_H);
}

void drawDivider(int y) {
  M5.Display.drawFastHLine(BODY_X, y, BODY_W, DARKGREY);
}

void drawHeader(const char *label, uint16_t color) {
  M5.Display.fillRect(0, 0, SCREEN_W, HEADER_H, BLACK);
  M5.Display.setCursor(4, 2);
  M5.Display.setTextSize(2);
  M5.Display.setTextColor(color, BLACK);
  M5.Display.print(label);
}

void clearBody() {
  M5.Display.fillRect(0, 20, SCREEN_W, SCREEN_H - 20, BLACK);
  M5.Display.setCursor(BODY_X, BODY_Y);
  M5.Display.setTextSize(1);
  M5.Display.setTextColor(WHITE, BLACK);
  M5.Display.setTextWrap(false);
}

void renderTodoMode() {
  drawHeader("Todo Mode", GREEN);
  clearBody();
  M5.Display.setCursor(BODY_X, BODY_Y);
  M5.Display.println("A rec  hold A asst");
  drawDivider(BODY_Y + 10);

  int y = BODY_Y + 16;
  M5.Display.setCursor(BODY_X, y);
  M5.Display.setTextColor(YELLOW, BLACK);
  M5.Display.println("OPEN TODOS");
  M5.Display.setTextColor(WHITE, BLACK);
  y += LINE_H + 1;

  if (todoCount == 0) {
    selectedTodoIndex = 0;
    y = drawWrappedText("No open todos yet.", BODY_X, y, BODY_W, 2);
  } else {
    if (selectedTodoIndex >= todoCount) {
      selectedTodoIndex = 0;
    }
    for (int i = 0; i < todoCount && y < BODY_Y + 66; i++) {
      String prefix = (i == selectedTodoIndex) ? "> " : "  ";
      String item = prefix + String(i + 1) + ". " + todoItems[i];
      y = drawWrappedText(item, BODY_X, y, BODY_W, 2) + 1;
    }
  }

  drawDivider(BODY_Y + 66);
  y = BODY_Y + 72;
  M5.Display.setCursor(BODY_X, y);
  M5.Display.setTextColor(CYAN, BLACK);
  M5.Display.println("HOLD B TO MARK DONE");
  M5.Display.setTextColor(WHITE, BLACK);
  y += LINE_H + 1;
  if (todoCount == 0) {
    drawWrappedText(lastNoteSummary, BODY_X, y, BODY_W, 3);
  } else {
    drawWrappedText("Short B selects next task.", BODY_X, y, BODY_W, 3);
  }
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
  M5.Display.setCursor(BODY_X, BODY_Y);
  if (assistantState == RECORDING) {
    M5.Display.println("A stop   B todo view");
  } else {
    M5.Display.println("A rec  hold A todo");
  }
  drawDivider(BODY_Y + 10);

  int y = BODY_Y + 16;
  M5.Display.setCursor(BODY_X, y);
  M5.Display.setTextColor(YELLOW, BLACK);
  M5.Display.println("YOU SAID");
  M5.Display.setTextColor(WHITE, BLACK);
  y += LINE_H + 1;
  drawWrappedText(lastTranscript.length() ? lastTranscript : "(waiting)", BODY_X, y, BODY_W, 3);

  drawDivider(BODY_Y + 54);
  y = BODY_Y + 60;
  M5.Display.setCursor(BODY_X, y);
  M5.Display.setTextColor(CYAN, BLACK);
  M5.Display.println("ASSISTANT");
  M5.Display.setTextColor(WHITE, BLACK);
  y += LINE_H + 1;
  drawWrappedText(lastResponse, BODY_X, y, BODY_W, 4);
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
    todoIds[i] = 0;
  }
  extractTodoIds(payload);
  todoCount = extractTodoTitles(payload);
  if (todoCount == 0) {
    selectedTodoIndex = 0;
  } else if (selectedTodoIndex >= todoCount) {
    selectedTodoIndex = 0;
  }
}

void selectNextTodo() {
  if (todoCount <= 1) {
    return;
  }
  selectedTodoIndex = (selectedTodoIndex + 1) % todoCount;
  renderScreen();
}

void processMicBuffer(int16_t *buffer, size_t sampleCount) {
  long absSum = 0;

  for (size_t i = 0; i < sampleCount; i++) {
    float raw = static_cast<float>(buffer[i]);

    // Track and remove slowly changing DC bias from the MEMS mic.
    dcEstimate = (dcEstimate * 0.995f) + (raw * 0.005f);
    float centered = raw - dcEstimate;

    // Light smoothing reduces sharp hiss without crushing speech.
    smoothSample = (smoothSample * 0.35f) + (centered * 0.65f);

    int16_t cleaned = static_cast<int16_t>(constrain(smoothSample, -32768.0f, 32767.0f));
    buffer[i] = cleaned;
    absSum += abs(cleaned);
  }

  int averageLevel = static_cast<int>(absSum / sampleCount);
  if (averageLevel < NOISE_GATE_LEVEL) {
    silentFrameCount++;
  } else {
    silentFrameCount = 0;
  }

  if (silentFrameCount >= MAX_SILENCE_FRAMES) {
    for (size_t i = 0; i < sampleCount; i++) {
      buffer[i] = 0;
    }
  }
}

void completeSelectedTodo() {
  if (WiFi.status() != WL_CONNECTED) {
    lastResponse = "WiFi required";
    renderScreen();
    return;
  }

  if (todoCount == 0 || selectedTodoIndex >= todoCount || todoIds[selectedTodoIndex] <= 0) {
    lastResponse = "No todo to mark done";
    renderScreen();
    return;
  }

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  String completedTitle = todoItems[selectedTodoIndex];
  String url = String(COMPLETE_TODO_URL_BASE) + String(todoIds[selectedTodoIndex]) + "/complete";

  if (!http.begin(client, url)) {
    lastResponse = "Done request failed";
    renderScreen();
    return;
  }

  http.addHeader("X-Device-API-Key", DEVICE_API_KEY);
  int code = http.POST("{}");
  http.end();

  if (code > 0 && code < 300) {
    lastResponse = "Done: " + completedTitle;
    fetchDeviceState();
    screenMode = MODE_TODOS;
  } else {
    lastResponse = "Done request error";
  }

  renderScreen();
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

  if (
    assistantState != RECORDING &&
    assistantState != PROCESSING &&
    M5.BtnA.pressedFor(700) &&
    !btnAHoldHandled
  ) {
    screenMode = (screenMode == MODE_TODOS) ? MODE_RESPONSE : MODE_TODOS;
    btnAHoldHandled = true;
    renderScreen();
  }

  if (
    screenMode == MODE_TODOS &&
    assistantState != RECORDING &&
    assistantState != PROCESSING &&
    M5.BtnB.pressedFor(500) &&
    !btnBHoldHandled
  ) {
    completeSelectedTodo();
    btnBHoldHandled = true;
  }

  if (M5.BtnB.wasReleased()) {
    if (
      screenMode == MODE_TODOS &&
      assistantState != RECORDING &&
      assistantState != PROCESSING &&
      !btnBHoldHandled
    ) {
      selectNextTodo();
    } else {
      renderScreen();
    }
    btnBHoldHandled = false;
  }

  if (M5.BtnA.wasPressed()) {
  }

  if (M5.BtnA.wasReleased()) {
    if (btnAHoldHandled) {
      btnAHoldHandled = false;
    } else if (assistantState == READY || assistantState == DISCONNECTED) {
      assistantState = RECORDING;
      screenMode = MODE_RESPONSE;
      firstResponseChunk = true;
      lastTranscript = "";
      lastResponse = "Listening...";
      dcEstimate = 0.0f;
      smoothSample = 0.0f;
      silentFrameCount = 0;
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
      processMicBuffer(buffer, MIC_BUF_LEN);
      ws.sendBIN((uint8_t *)buffer, sizeof(buffer));
    }
  }
}
