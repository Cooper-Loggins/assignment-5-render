#include <M5Unified.h>
#include <Preferences.h>
#include <WiFi.h>

Preferences prefs;

// Temporary local Wi-Fi credentials. Remove these again before submission.
const char* WIFI_SSID = "WhiteSky-TheConnectionAthens";
const char* WIFI_USERNAME = "";
const char* WIFI_PASSWORD = "GoDawgs255";

void showMessage(const char* line1, const char* line2 = "", const char* line3 = "", uint16_t color = WHITE) {
  M5.Display.fillScreen(BLACK);
  M5.Display.setCursor(4, 20);
  M5.Display.setTextSize(2);
  M5.Display.setTextColor(color, BLACK);
  M5.Display.println(line1);
  if (strlen(line2) > 0) {
    M5.Display.println();
    M5.Display.println(line2);
  }
  if (strlen(line3) > 0) {
    M5.Display.println();
    M5.Display.println(line3);
  }
}

bool testWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(500);
  if (strlen(WIFI_USERNAME) > 0) {
    WiFi.begin(WIFI_SSID, WPA2_AUTH_PEAP, WIFI_USERNAME, WIFI_USERNAME, WIFI_PASSWORD);
  } else {
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }

  showMessage("Saving WiFi...", "Testing connect");

  for (int i = 0; i < 20; i++) {
    if (WiFi.status() == WL_CONNECTED) {
      return true;
    }
    delay(1000);
    M5.Display.print(".");
  }

  return WiFi.status() == WL_CONNECTED;
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(1);

  prefs.begin("wifi", false);
  prefs.putUChar("count", 1);
  prefs.putString("ssid0", WIFI_SSID);
  prefs.putString("user0", WIFI_USERNAME);
  prefs.putString("pass0", WIFI_PASSWORD);
  prefs.end();

  if (testWiFi()) {
    String ip = WiFi.localIP().toString();
    showMessage("WiFi saved.", "Connected OK", ip.c_str(), GREEN);
  } else {
    showMessage("WiFi saved.", "Connect failed", "Check SSID/pass", RED);
  }
}

void loop() {
}
