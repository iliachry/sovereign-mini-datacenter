// Host stub of paulstoffregen/OneWire: the sketch only constructs the bus.
#pragma once
#include <cstdint>

class OneWire {
 public:
  explicit OneWire(uint8_t pin) : testPin(pin) {}
  uint8_t testPin;
};
