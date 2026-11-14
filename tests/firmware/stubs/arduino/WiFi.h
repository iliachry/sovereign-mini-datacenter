// Host stub of the ESP32 WiFi API used by esp32_telemetry_bridge.ino.
#pragma once
#include <string>

typedef enum { WIFI_OFF = 0, WIFI_STA = 1, WIFI_AP = 2, WIFI_AP_STA = 3 } wifi_mode_t;

class WiFiClass {
 public:
  bool mode(wifi_mode_t m) {
    testMode = m;
    return true;
  }
  int begin(const char *ssid, const char *passphrase = nullptr) {
    testSsid = ssid ? ssid : "";
    testPassphrase = passphrase ? passphrase : "";
    testBeginCalls++;
    return 0;
  }

  // test hooks
  wifi_mode_t testMode = WIFI_OFF;
  std::string testSsid;
  std::string testPassphrase;
  int testBeginCalls = 0;
};

inline WiFiClass WiFi;
