// Host-side stand-in for the Arduino-ESP32 core, sized for exactly what
// firmware/esp32_telemetry_bridge.ino uses. It lets the UNMODIFIED sketch be
// compiled and exercised with a plain C++17 compiler (see ../../run.sh).
//
// Deliberately NOT provided: Stream::readBytes / readBytesUntil / readString /
// readStringUntil / parseInt / setTimeout. Those block on a timeout; if the
// sketch ever starts using them the host build fails, which is the point.
#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <string>

// ── Test clock ───────────────────────────────────────────────────────────────
// millis() only moves when the test moves it, or when the code under test
// calls delay(). A parser that "waits" for data therefore shows up as an
// unexpected clock advance.
namespace smdc_host {
inline unsigned long now_ms = 0;        // value returned by millis()
inline unsigned long delayed_ms = 0;    // total time spent inside delay()
inline unsigned long delay_calls = 0;
}  // namespace smdc_host

// NOTE: on the ESP32 `unsigned long` is 32 bits; on a 64-bit host it is 64.
// The 49.7-day millis() wrap can therefore not be reproduced here.
inline unsigned long millis() { return smdc_host::now_ms; }
inline void delay(unsigned long ms) {
  smdc_host::now_ms += ms;
  smdc_host::delayed_ms += ms;
  smdc_host::delay_calls++;
}

// ── GPIO ─────────────────────────────────────────────────────────────────────
#define INPUT 0x01
#define OUTPUT 0x03
#define LOW 0x0
#define HIGH 0x1
inline void pinMode(uint8_t, uint8_t) {}
inline void digitalWrite(uint8_t, uint8_t) {}

// ── String ───────────────────────────────────────────────────────────────────
class String {
 public:
  String(const char *s = "") : s_(s ? s : "") {}
  String(const std::string &s) : s_(s) {}
  String(float value, unsigned int decimalPlaces = 2) { fmtFloat(value, decimalPlaces); }
  String(double value, unsigned int decimalPlaces = 2) { fmtFloat(value, decimalPlaces); }
  String(int value) : s_(std::to_string(value)) {}
  String(unsigned int value) : s_(std::to_string(value)) {}
  String(long value) : s_(std::to_string(value)) {}
  String(unsigned long value) : s_(std::to_string(value)) {}

  bool reserve(unsigned int size) {
    s_.reserve(size);
    return true;
  }
  unsigned int length() const { return (unsigned int)s_.size(); }
  const char *c_str() const { return s_.c_str(); }
  const std::string &std_str() const { return s_; }

  String &operator+=(const String &rhs) {
    s_ += rhs.s_;
    return *this;
  }
  String &operator+=(const char *rhs) {
    s_ += rhs;
    return *this;
  }
  String &operator+=(char rhs) {
    s_ += rhs;
    return *this;
  }
  bool operator==(const String &rhs) const { return s_ == rhs.s_; }
  bool operator==(const char *rhs) const { return s_ == rhs; }

 private:
  // Arduino formats floats with dtostrf(); fixed notation, rounded.
  void fmtFloat(double value, unsigned int decimalPlaces) {
    char buf[64];
    snprintf(buf, sizeof(buf), "%.*f", (int)decimalPlaces, value);
    s_ = buf;
  }
  std::string s_;
};

inline String operator+(const String &a, const String &b) {
  String r(a);
  r += b;
  return r;
}
inline String operator+(const String &a, const char *b) {
  String r(a);
  r += b;
  return r;
}
inline String operator+(const char *a, const String &b) {
  String r(a);
  r += b;
  return r;
}

// ── HardwareSerial ───────────────────────────────────────────────────────────
#define SERIAL_8N1 0x800001c

class HardwareSerial {
 public:
  explicit HardwareSerial(int uartNr) : uartNr_(uartNr) {}

  void begin(unsigned long baud, uint32_t config = SERIAL_8N1, int8_t rxPin = -1, int8_t txPin = -1) {
    baud_ = baud;
    config_ = config;
    rxPin_ = rxPin;
    txPin_ = txPin;
    begun_ = true;
  }
  int available() { return (int)rx_.size(); }
  // Like the real core: returns -1 immediately when nothing is buffered.
  int read() {
    if (rx_.empty()) {
      return -1;
    }
    uint8_t b = rx_.front();
    rx_.pop_front();
    bytesRead_++;
    return b;
  }

  size_t print(const char *s) {
    tx_ += s;
    return strlen(s);
  }
  size_t print(const String &s) {
    tx_ += s.std_str();
    return s.length();
  }
  size_t println() {
    tx_ += "\r\n";
    return 2;
  }
  size_t println(const char *s) { return print(s) + println(); }
  size_t println(const String &s) { return print(s) + println(); }

  // ── test hooks (not part of the Arduino API) ──
  void testFeed(const uint8_t *data, size_t len) { rx_.insert(rx_.end(), data, data + len); }
  size_t testPending() const { return rx_.size(); }
  unsigned long testBytesRead() const { return bytesRead_; }
  const std::string &testOutput() const { return tx_; }
  bool testBegun() const { return begun_; }
  unsigned long testBaud() const { return baud_; }
  uint32_t testConfig() const { return config_; }
  int testRxPin() const { return rxPin_; }
  int testTxPin() const { return txPin_; }
  int testUartNr() const { return uartNr_; }

 private:
  int uartNr_;
  bool begun_ = false;
  unsigned long baud_ = 0;
  uint32_t config_ = 0;
  int rxPin_ = -1;
  int txPin_ = -1;
  unsigned long bytesRead_ = 0;
  std::deque<uint8_t> rx_;
  std::string tx_;
};

inline HardwareSerial Serial(0);
