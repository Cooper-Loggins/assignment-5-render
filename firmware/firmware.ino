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
#define MIC_BUF_LEN 1024
#define STATE_REFRESH_MS 60000
#define MAX_TODO_ITEMS 5
#define MAX_WRAP_LINES 24
#define STOP_TAIL_MS 900

WebSocketsClient ws;
Preferences prefs;
String wsExtraHeaders;
bool wifiWasConnected = false;

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
String tempStatusMessage = "";
String transcriptDisplayLines[MAX_WRAP_LINES];
String responseDisplayLines[MAX_WRAP_LINES];

bool firstResponseChunk = true;
unsigned long lastStateRefresh = 0;
bool btnBHoldHandled = false;
bool btnAHoldHandled = false;
bool stopRequested = false;
unsigned long stopRequestedAt = 0;
unsigned long tempStatusUntil = 0;
uint16_t tempStatusColor = WHITE;
int transcriptScrollOffset = 0;
int responseScrollOffset = 0;
float micDcEstimate = 0.0f;

const int SCREEN_W = 240;
const int SCREEN_H = 135;
const int HEADER_H = 18;
const int BODY_X = 6;
const int BODY_Y = 22;
const int BODY_W = 228;
const int LINE_H = 11;
const int TODO_SECTION_BOTTOM = BODY_Y + 64;
const int RESPONSE_TRANSCRIPT_VISIBLE_LINES = 2;
const int RESPONSE_ASSISTANT_VISIBLE_LINES = 3;

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

String sanitizeDisplayText(String text) {
  String clean = "";
  for (int i = 0; i < text.length(); i++) {
    unsigned char c = static_cast<unsigned char>(text[i]);
    if (c >= 32 && c <= 126) {
      clean += static_cast<char>(c);
    } else if (c == '\n' || c == '\r' || c == '\t') {
      clean += ' ';
    }
  }
  return compactText(clean);
}

int drawWrappedText(const String &rawText, int x, int y, int maxWidth, int maxLines) {
  String text = sanitizeDisplayText(rawText);
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

int buildWrappedLines(const String &rawText, int maxWidth, String lines[], int maxLines) {
  String text = sanitizeDisplayText(rawText);
  if (text.isEmpty()) {
    return 0;
  }

  const int maxChars = maxWidth / 6;
  int start = 0;
  int lineCount = 0;

  while (start < text.length() && lineCount < maxLines) {
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
    lines[lineCount++] = part;
    start += take;

    while (start < text.length() && text[start] == ' ') {
      start++;
    }
  }

  return lineCount;
}

int drawWrappedTextWindow(
  const String &rawText,
  String lines[],
  int x,
  int y,
  int maxWidth,
  int startLine,
  int visibleLines
) {
  int lineCount = buildWrappedLines(rawText, maxWidth, lines, MAX_WRAP_LINES);
  if (lineCount == 0) {
    return 0;
  }

  int maxStartLine = max(0, lineCount - visibleLines);
  int clampedStart = constrain(startLine, 0, maxStartLine);
  int drawn = 0;

  for (int i = clampedStart; i < lineCount && drawn < visibleLines; i++) {
    M5.Display.setCursor(x, y + (drawn * LINE_H));
    M5.Display.println(lines[i]);
    drawn++;
  }

  return lineCount;
}

void drawDivider(int y) {
  M5.Display.drawFastHLine(BODY_X, y, BODY_W, DARKGREY);
}

bool hasTemporaryStatus() {
  return tempStatusMessage.length() > 0 && millis() < tempStatusUntil;
}

void setTemporaryStatus(const String &message, uint16_t color, unsigned long durationMs = 2200) {
  tempStatusMessage = sanitizeDisplayText(message);
  tempStatusColor = color;
  tempStatusUntil = millis() + durationMs;
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
    const int todoHintY = TODO_SECTION_BOTTOM - LINE_H + 2;
    const int todoTextBottom = todoHintY - 3;
    const int visibleTodoLines = max(1, (todoTextBottom - y) / LINE_H);
    String item = "> " + String(selectedTodoIndex + 1) + "/" + String(todoCount) + "  " +
                  todoItems[selectedTodoIndex];
    int todoLineCount = drawWrappedTextWindow(
      item,
      transcriptDisplayLines,
      BODY_X,
      y,
      BODY_W,
      0,
      visibleTodoLines
    );

    M5.Display.fillRect(0, todoHintY - 1, SCREEN_W, LINE_H + 3, BLACK);
    M5.Display.setCursor(BODY_X, todoHintY);
    M5.Display.setTextColor(ORANGE, BLACK);
    if (todoLineCount > visibleTodoLines) {
      M5.Display.println("todo continues, press B");
    } else if (todoCount > 1) {
      M5.Display.println("keep scrolling");
    } else {
      M5.Display.println("only todo item");
    }
    M5.Display.setTextColor(WHITE, BLACK);
  }

  drawDivider(BODY_Y + 66);
  y = BODY_Y + 72;
  M5.Display.setCursor(BODY_X, y);
  if (hasTemporaryStatus()) {
    M5.Display.setTextColor(tempStatusColor, BLACK);
    M5.Display.println("STATUS");
    M5.Display.setTextColor(WHITE, BLACK);
    y += LINE_H + 1;
    drawWrappedText(tempStatusMessage, BODY_X, y, BODY_W, 3);
  } else {
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
    M5.Display.println("A stop   B cancel");
  } else {
    M5.Display.println("A rec  B next");
  }
  drawDivider(BODY_Y + 10);

  int y = BODY_Y + 16;
  M5.Display.setCursor(BODY_X, y);
  M5.Display.setTextColor(YELLOW, BLACK);
  M5.Display.println("YOU SAID");
  M5.Display.setTextColor(WHITE, BLACK);
  y += LINE_H + 1;
  int transcriptLines = drawWrappedTextWindow(
    lastTranscript.length() ? lastTranscript : "(waiting)",
    transcriptDisplayLines,
    BODY_X,
    y,
    BODY_W,
    transcriptScrollOffset,
    RESPONSE_TRANSCRIPT_VISIBLE_LINES
  );
  int maxTranscriptOffset = max(0, transcriptLines - RESPONSE_TRANSCRIPT_VISIBLE_LINES);
  transcriptScrollOffset = constrain(transcriptScrollOffset, 0, maxTranscriptOffset);
  if (maxTranscriptOffset > 0) {
    M5.Display.setCursor(BODY_X + 176, BODY_Y + 16);
    M5.Display.setTextColor(ORANGE, BLACK);
    M5.Display.print(String(transcriptScrollOffset + 1) + "/" + String(maxTranscriptOffset + 1));
    M5.Display.setTextColor(WHITE, BLACK);
  }

  drawDivider(BODY_Y + 54);
  y = BODY_Y + 60;
  M5.Display.setCursor(BODY_X, y);
  M5.Display.setTextColor(CYAN, BLACK);
  if (hasTemporaryStatus()) {
    M5.Display.setTextColor(tempStatusColor, BLACK);
    M5.Display.println("STATUS");
  } else {
    M5.Display.println("ASSISTANT");
  }
  M5.Display.setTextColor(WHITE, BLACK);
  y += LINE_H + 1;
  int responseLines = drawWrappedTextWindow(
    hasTemporaryStatus() ? tempStatusMessage : lastResponse,
    responseDisplayLines,
    BODY_X,
    y,
    BODY_W,
    responseScrollOffset,
    RESPONSE_ASSISTANT_VISIBLE_LINES
  );
  int maxResponseOffset = max(0, responseLines - RESPONSE_ASSISTANT_VISIBLE_LINES);
  responseScrollOffset = constrain(responseScrollOffset, 0, maxResponseOffset);
  if (maxResponseOffset > 0) {
    M5.Display.setCursor(BODY_X, BODY_Y + 104);
    M5.Display.setTextColor(ORANGE, BLACK);
    M5.Display.print("B wraps through long text");
    M5.Display.setTextColor(WHITE, BLACK);
  }
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
    setTemporaryStatus("Server unavailable", RED);
    return;
  }

  http.addHeader("X-Device-API-Key", DEVICE_API_KEY);
  int code = http.GET();
  if (code <= 0) {
    http.end();
    lastResponse = "State GET error";
    setTemporaryStatus("Server unavailable", RED);
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

void scrollResponseView(int delta) {
  bool changed = false;

  int transcriptLines = buildWrappedLines(
    lastTranscript.length() ? lastTranscript : "(waiting)",
    BODY_W,
    transcriptDisplayLines,
    MAX_WRAP_LINES
  );
  int responseLines = buildWrappedLines(
    hasTemporaryStatus() ? tempStatusMessage : lastResponse,
    BODY_W,
    responseDisplayLines,
    MAX_WRAP_LINES
  );

  int maxTranscriptOffset = max(0, transcriptLines - RESPONSE_TRANSCRIPT_VISIBLE_LINES);
  int maxResponseOffset = max(0, responseLines - RESPONSE_ASSISTANT_VISIBLE_LINES);

  int nextTranscriptOffset = transcriptScrollOffset;
  int nextResponseOffset = responseScrollOffset;

  if (delta > 0 && maxTranscriptOffset > 0) {
    nextTranscriptOffset = (transcriptScrollOffset >= maxTranscriptOffset)
      ? 0
      : transcriptScrollOffset + 1;
  }
  if (delta > 0 && maxResponseOffset > 0) {
    nextResponseOffset = (responseScrollOffset >= maxResponseOffset)
      ? 0
      : responseScrollOffset + 1;
  }

  if (nextTranscriptOffset != transcriptScrollOffset) {
    transcriptScrollOffset = nextTranscriptOffset;
    changed = true;
  }
  if (nextResponseOffset != responseScrollOffset) {
    responseScrollOffset = nextResponseOffset;
    changed = true;
  }

  if (changed) {
    renderScreen();
  }
}

void processMicBuffer(int16_t *buffer, size_t sampleCount) {
  for (size_t i = 0; i < sampleCount; i++) {
    float sample = static_cast<float>(buffer[i]);

    // Remove slow DC drift but otherwise preserve the raw speech waveform.
    micDcEstimate = (micDcEstimate * 0.995f) + (sample * 0.005f);
    float centered = sample - micDcEstimate;

    // Apply a very gentle limiter only near the extremes to avoid hard clipping.
    if (centered > 28000.0f) {
      centered = 28000.0f + ((centered - 28000.0f) * 0.2f);
    } else if (centered < -28000.0f) {
      centered = -28000.0f + ((centered + 28000.0f) * 0.2f);
    }

    if (centered > 32767.0f) {
      centered = 32767.0f;
    } else if (centered < -32768.0f) {
      centered = -32768.0f;
    }

    buffer[i] = static_cast<int16_t>(centered);
  }
}

void completeSelectedTodo() {
  if (WiFi.status() != WL_CONNECTED) {
    lastResponse = "WiFi required";
    setTemporaryStatus("WiFi required", RED);
    renderScreen();
    return;
  }

  if (todoCount == 0 || selectedTodoIndex >= todoCount || todoIds[selectedTodoIndex] <= 0) {
    lastResponse = "No todo to mark done";
    setTemporaryStatus("No todo selected", YELLOW);
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
    setTemporaryStatus("Server unavailable", RED);
    renderScreen();
    return;
  }

  http.addHeader("X-Device-API-Key", DEVICE_API_KEY);
  int code = http.POST("{}");
  http.end();

  if (code > 0 && code < 300) {
    lastResponse = "Done: " + completedTitle;
    setTemporaryStatus("Marked done", GREEN);
    fetchDeviceState();
    screenMode = MODE_TODOS;
  } else {
    lastResponse = "Done request error";
    setTemporaryStatus("Done request error", RED);
  }

  renderScreen();
}

void onWebSocket(WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      assistantState = DISCONNECTED;
      setTemporaryStatus("Server unavailable", RED);
      renderScreen();
      break;

    case WStype_CONNECTED:
      assistantState = READY;
      lastResponse = "Ready for notes";
      transcriptScrollOffset = 0;
      responseScrollOffset = 0;
      setTemporaryStatus("Connected", GREEN, 1500);
      renderScreen();
      break;

    case WStype_TEXT: {
      String msg((char *)payload);

      if (msg.startsWith("T:")) {
        assistantState = PROCESSING;
        lastTranscript = msg.substring(2);
        transcriptScrollOffset = 0;
        lastResponse = "";
        responseScrollOffset = 0;
        firstResponseChunk = true;
      } else if (msg.startsWith("R:")) {
        if (firstResponseChunk) {
          lastResponse = "";
          responseScrollOffset = 0;
          firstResponseChunk = false;
        }
        lastResponse += msg.substring(2);
        if (msg.indexOf("Unauthorized") >= 0) {
          setTemporaryStatus("Auth failed", RED, 2500);
        }
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

void connectAssistantSocket() {
  ws.disconnect();
  ws.beginSSL(SERVER_HOST, SERVER_PORT, WS_PATH);
  ws.onEvent(onWebSocket);
  ws.setReconnectInterval(3000);
}

void markSocketUnavailable(const String &message = "Server unavailable") {
  assistantState = DISCONNECTED;
  stopRequested = false;
  stopRequestedAt = 0;
  setTemporaryStatus(message, RED, 2400);
  renderScreen();
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

  wifiWasConnected = true;
  M5.Speaker.end();
  M5.Mic.begin();

  connectAssistantSocket();

  fetchDeviceState();
  renderScreen();
}

void loop() {
  M5.update();
  ws.loop();

  bool wifiConnected = WiFi.status() == WL_CONNECTED;
  if (!wifiConnected) {
    if (wifiWasConnected) {
      wifiWasConnected = false;
      assistantState = DISCONNECTED;
      stopRequested = false;
      setTemporaryStatus("WiFi reconnecting", YELLOW, 2000);
      ws.disconnect();
      renderScreen();
    }
    connectWiFi();
    wifiConnected = WiFi.status() == WL_CONNECTED;
  }

  if (wifiConnected && !wifiWasConnected) {
    wifiWasConnected = true;
    assistantState = DISCONNECTED;
    setTemporaryStatus("WiFi back, reconnecting", YELLOW, 2200);
    connectAssistantSocket();
    fetchDeviceState();
    renderScreen();
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
    if (assistantState == RECORDING) {
      assistantState = READY;
      stopRequested = false;
      ws.sendTXT("cancel");
      lastResponse = "Recording canceled.";
      responseScrollOffset = 0;
      setTemporaryStatus("Canceled", YELLOW, 1800);
      renderScreen();
    } else if (
      screenMode == MODE_TODOS &&
      assistantState != RECORDING &&
      assistantState != PROCESSING &&
      !btnBHoldHandled
    ) {
      selectNextTodo();
    } else if (
      screenMode == MODE_RESPONSE &&
      assistantState != RECORDING &&
      assistantState != PROCESSING &&
      !btnBHoldHandled
    ) {
      scrollResponseView(1);
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
    } else if (assistantState == READY) {
      if (!ws.sendTXT("start")) {
        markSocketUnavailable("Socket start failed");
      } else {
        assistantState = RECORDING;
        screenMode = MODE_RESPONSE;
        firstResponseChunk = true;
        stopRequested = false;
        stopRequestedAt = 0;
        micDcEstimate = 0.0f;
        lastTranscript = "";
        transcriptScrollOffset = 0;
        lastResponse = "Listening...";
        responseScrollOffset = 0;
        setTemporaryStatus("Recording...", RED, 2000);
        renderScreen();
      }
    } else if (assistantState == DISCONNECTED) {
      setTemporaryStatus("Wait for server reconnect", YELLOW, 2200);
      renderScreen();
    } else if (assistantState == RECORDING) {
      stopRequested = true;
      stopRequestedAt = millis();
      setTemporaryStatus("Finishing...", YELLOW, 1200);
      renderScreen();
    }
  }

  if (assistantState == RECORDING) {
    int16_t buffer[MIC_BUF_LEN];
    if (M5.Mic.record(buffer, MIC_BUF_LEN, SAMPLE_RATE)) {
      processMicBuffer(buffer, MIC_BUF_LEN);
      if (!ws.sendBIN((uint8_t *)buffer, sizeof(buffer))) {
        markSocketUnavailable("Audio upload failed");
        return;
      }
    }

    if (stopRequested && millis() - stopRequestedAt >= STOP_TAIL_MS) {
      if (!ws.sendTXT("stop")) {
        markSocketUnavailable("Stop upload failed");
        return;
      }
      assistantState = PROCESSING;
      stopRequested = false;
      stopRequestedAt = 0;
      setTemporaryStatus("Uploading...", YELLOW, 2500);
      renderScreen();
    }
  }
}
