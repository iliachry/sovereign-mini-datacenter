// Host stub of milesburton/DallasTemperature with programmable probes.
#pragma once
#include <Arduino.h>
#include <OneWire.h>

// Same sentinel as the real library.
#define DEVICE_DISCONNECTED_C -127

class DallasTemperature {
 public:
  static const int TEST_MAX_PROBES = 4;

  explicit DallasTemperature(OneWire *bus) : bus_(bus) {
    for (int i = 0; i < TEST_MAX_PROBES; i++) {
      probe_[i] = DEVICE_DISCONNECTED_C;
      latched_[i] = DEVICE_DISCONNECTED_C;
    }
  }

  void begin() { begun_ = true; }
  void setWaitForConversion(bool wait) { waitForConversion_ = wait; }

  // Starts a conversion. As in the real library this BLOCKS for the 12-bit
  // conversion time (750 ms) unless setWaitForConversion(false) was called -
  // the stub models that by burning test-clock time through delay().
  void requestTemperatures() {
    requests_++;
    for (int i = 0; i < TEST_MAX_PROBES; i++) {
      latched_[i] = probe_[i];
    }
    if (waitForConversion_) {
      delay(750);
    }
  }

  // Result of the last conversion; -127 for a probe that did not answer.
  float getTempCByIndex(uint8_t index) {
    reads_++;
    if (index >= TEST_MAX_PROBES) {
      return DEVICE_DISCONNECTED_C;
    }
    return latched_[index];
  }

  // ── test hooks ──
  void testSetProbe(int index, float celsius) { probe_[index] = celsius; }
  void testDisconnectProbe(int index) { probe_[index] = DEVICE_DISCONNECTED_C; }
  bool testBegun() const { return begun_; }
  bool testWaitForConversion() const { return waitForConversion_; }
  unsigned long testRequests() const { return requests_; }
  unsigned long testReads() const { return reads_; }
  OneWire *testBus() const { return bus_; }

 private:
  OneWire *bus_;
  bool begun_ = false;
  bool waitForConversion_ = true;  // library default
  unsigned long requests_ = 0;
  unsigned long reads_ = 0;
  float probe_[TEST_MAX_PROBES];
  float latched_[TEST_MAX_PROBES];
};
