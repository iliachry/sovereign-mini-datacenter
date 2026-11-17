/*
 * Sovereign Mini Datacenter - ESP32 Hardware Telemetry & Micro-Grid Bridge
 * (Arduino flavour)
 *
 * What this sketch implements:
 *  - UART1 (GPIO 16 RX / GPIO 17 TX): ONE Victron VE.Direct device, text protocol,
 *    19200 baud 8N1, with per-frame checksum validation.
 *      * A SmartShunt / BMV battery monitor reports V, I and SOC (no PPV).
 *      * A SmartSolar / BlueSolar MPPT reports V, I and PPV. An MPPT never
 *        sends SOC, so the SoC metric stays absent when an MPPT is connected.
 *    Reading both devices needs a second UART, which this sketch does not have.
 *  - GPIO 4 (1-Wire): two Dallas DS18B20 temperature probes (coolant loop),
 *    converted in the background (non-blocking).
 *  - WebServer :80  /metrics (Prometheus text format) and /health
 *  - USB serial (115200 baud): periodic status log
 *
 * NOT implemented in this flavour: RS485 / Modbus BMS link, MQTT, SSD1306 OLED.
 * Use firmware/esphome_smdc_bridge.yaml if you need MQTT, the Home Assistant
 * API or the OLED status display.
 *
 * A metric is only exported after it has been measured: nothing is reported
 * until a checksum-valid VE.Direct frame (or a successful DS18B20 conversion)
 * has set it, and a value is withdrawn again once it is older than the
 * staleness limit below.
 *
 * Wi-Fi credentials: copy secrets.h.example to secrets.h (git-ignored) next to
 * this sketch and fill it in. Without secrets.h the sketch still compiles, but
 * with placeholder credentials that cannot join any network.
 */

#include <WiFi.h>
#include <WebServer.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if __has_include("secrets.h")
#include "secrets.h"
#else
// Placeholders only - create secrets.h from secrets.h.example.
#define WIFI_SSID "REPLACE_WITH_WIFI_SSID"
#define WIFI_PASSWORD "REPLACE_WITH_WIFI_PASSWORD"
#endif

// Hardware Pins
#define ONE_WIRE_BUS 4
#define RX_VE_DIRECT 16
#define TX_VE_DIRECT 17
#define STATUS_LED_PIN 2

// Timing
#define VE_STALE_MS 10000UL        // VE.Direct sends a frame every second
#define TEMP_STALE_MS 15000UL
#define TEMP_PERIOD_MS 2000UL      // one DS18B20 conversion cycle
#define TEMP_CONVERSION_MS 800UL   // 12-bit conversion takes up to 750 ms
#define STATUS_LOG_MS 3000UL

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
HardwareSerial veSerial(1);
WebServer server(80);

// ── Telemetry state ──────────────────────────────────────────────────────────
// Index of every reading. The first VE_FIELD_COUNT entries come from VE.Direct.
#define R_BATTERY_VOLTAGE 0
#define R_BATTERY_CURRENT 1
#define R_SOLAR_POWER 2
#define R_BATTERY_SOC 3
#define VE_FIELD_COUNT 4
#define R_COOLANT_INTAKE 4
#define R_COOLANT_EXHAUST 5
#define READING_COUNT 6

float readingValue[READING_COUNT] = {NAN, NAN, NAN, NAN, NAN, NAN};
unsigned long readingStamp[READING_COUNT] = {0, 0, 0, 0, 0, 0};
bool readingSet[READING_COUNT] = {false, false, false, false, false, false};

// ── VE.Direct text-protocol frame parser ─────────────────────────────────────
// A frame is a block of "\r\n<Label>\t<Value>" fields that ends with the field
// "Checksum\t<byte>". The modulo-256 sum of every byte of the block, including
// that final checksum byte, is 0 for an intact frame. HEX-protocol messages
// (":....\n") may be interleaved and are not part of the checksum.
#define VE_IDLE 0
#define VE_RECORD_BEGIN 1
#define VE_RECORD_NAME 2
#define VE_RECORD_VALUE 3
#define VE_CHECKSUM 4
#define VE_RECORD_HEX 5

uint8_t veState = VE_IDLE;
uint8_t veChecksum = 0;
char veName[12];
char veValue[36];
uint8_t veNameLen = 0;
uint8_t veValueLen = 0;

// Fields of the frame being received; committed only if its checksum is valid.
float veStagedValue[VE_FIELD_COUNT];
bool veStagedSet[VE_FIELD_COUNT] = {false, false, false, false};

bool veFrameSeen = false;
bool veFrameStale = false;
unsigned long veLastFrameMs = 0;
uint32_t veAgeAnchorMs = 0;
uint64_t veAgeAccumMs = 0;
unsigned long veFramesValid = 0;
unsigned long veChecksumErrors = 0;

// ── DS18B20 background conversion ────────────────────────────────────────────
bool tempConversionPending = false;
unsigned long tempRequestMs = 0;

unsigned long lastStatusLog = 0;

void veClearStaged() {
  for (int i = 0; i < VE_FIELD_COUNT; i++) {
    veStagedSet[i] = false;
  }
}

void veStageField() {
  int idx = -1;
  float scale = 1.0f;
  if (strcmp(veName, "V") == 0) {
    idx = R_BATTERY_VOLTAGE;
    scale = 0.001f;  // mV -> V
  } else if (strcmp(veName, "I") == 0) {
    idx = R_BATTERY_CURRENT;
    scale = 0.001f;  // mA -> A
  } else if (strcmp(veName, "PPV") == 0) {
    idx = R_SOLAR_POWER;  // W
  } else if (strcmp(veName, "SOC") == 0) {
    idx = R_BATTERY_SOC;
    scale = 0.1f;  // per mille -> %
  }
  if (idx < 0) {
    return;
  }
  // Devices send "---" while a value is unavailable; accept integers only.
  char *end = NULL;
  long raw = strtol(veValue, &end, 10);
  if (end == veValue || *end != '\0') {
    return;
  }
  veStagedValue[idx] = (float)raw * scale;
  veStagedSet[idx] = true;
}

void veCommitFrame() {
  unsigned long now = millis();
  for (int i = 0; i < VE_FIELD_COUNT; i++) {
    if (veStagedSet[i]) {
      readingValue[i] = veStagedValue[i];
      readingStamp[i] = now;
      readingSet[i] = true;
    }
  }
  veFrameSeen = true;
  veFrameStale = false;
  veLastFrameMs = now;
  veAgeAnchorMs = (uint32_t)now;
  veAgeAccumMs = 0;
  veFramesValid++;
}

// Feed one received byte into the frame parser. Never blocks.
void veHandleByte(uint8_t b) {
  if (b == ':' && veState != VE_CHECKSUM && veState != VE_RECORD_HEX) {
    veState = VE_RECORD_HEX;
  }
  if (veState != VE_RECORD_HEX) {
    veChecksum = (uint8_t)(veChecksum + b);
  }

  switch (veState) {
    case VE_IDLE:
      // Wait for the line feed that starts the next field.
      if (b == '\n') {
        veState = VE_RECORD_BEGIN;
      }
      break;

    case VE_RECORD_BEGIN:
      veNameLen = 0;
      veName[veNameLen++] = (char)b;
      veState = VE_RECORD_NAME;
      break;

    case VE_RECORD_NAME:
      if (b == '\t') {
        veName[veNameLen] = '\0';
        if (strcmp(veName, "Checksum") == 0) {
          veState = VE_CHECKSUM;
        } else {
          veValueLen = 0;
          veState = VE_RECORD_VALUE;
        }
      } else if (veNameLen < sizeof(veName) - 1) {
        veName[veNameLen++] = (char)b;
      }
      break;

    case VE_RECORD_VALUE:
      if (b == '\n') {
        veValue[veValueLen] = '\0';
        veStageField();
        veState = VE_RECORD_BEGIN;
      } else if (b != '\r' && veValueLen < sizeof(veValue) - 1) {
        veValue[veValueLen++] = (char)b;
      }
      break;

    case VE_CHECKSUM:
      // b is the checksum byte and has already been added to the sum.
      if (veChecksum == 0) {
        veCommitFrame();
      } else {
        veChecksumErrors++;
      }
      veClearStaged();
      veChecksum = 0;
      veState = VE_IDLE;
      break;

    case VE_RECORD_HEX:
      // Skip the HEX message; the interrupted text frame is discarded.
      if (b == '\n') {
        veClearStaged();
        veChecksum = 0;
        veState = VE_IDLE;
      }
      break;

    default:
      veState = VE_IDLE;
      break;
  }
}

void storeTemperature(int idx, float celsius) {
  // DEVICE_DISCONNECTED_C (-127) means "no probe answered" - not a temperature.
  if (celsius == DEVICE_DISCONNECTED_C) {
    return;
  }
  readingValue[idx] = celsius;
  readingStamp[idx] = millis();
  readingSet[idx] = true;
}

// Start a conversion, then collect it later; never waits for the sensor.
void serviceTemperatures() {
  unsigned long now = millis();
  if (tempConversionPending) {
    if ((uint32_t)(now - tempRequestMs) >= (uint32_t)TEMP_CONVERSION_MS) {
      // Index order follows the 1-Wire address enumeration, not the physical
      // position: check which probe is index 0 before trusting the labels.
      storeTemperature(R_COOLANT_INTAKE, sensors.getTempCByIndex(0));
      storeTemperature(R_COOLANT_EXHAUST, sensors.getTempCByIndex(1));
      tempConversionPending = false;
    }
  } else if ((uint32_t)(now - tempRequestMs) >= (uint32_t)TEMP_PERIOD_MS) {
    sensors.requestTemperatures();  // returns immediately (setWaitForConversion(false))
    tempRequestMs = now;
    tempConversionPending = true;
  }
}

uint64_t currentVeFrameAgeMs() {
  if (!veFrameSeen) {
    return 0;
  }
  unsigned long now = millis();
  uint32_t now32 = (uint32_t)now;
  veAgeAccumMs += (uint32_t)(now32 - veAgeAnchorMs);
  veAgeAnchorMs = now32;
  unsigned long rawDiff = now - veLastFrameMs;
  if ((uint64_t)rawDiff > veAgeAccumMs) {
    veAgeAccumMs = (uint64_t)rawDiff;
  }
  if (veFrameStale && veAgeAccumMs <= (uint64_t)VE_STALE_MS) {
    veAgeAccumMs += (1ULL << 32);
  }
  return veAgeAccumMs;
}

bool readingFresh(int idx, unsigned long maxAgeMs) {
  if (!readingSet[idx]) {
    return false;
  }
  unsigned long now = millis();
  if ((uint32_t)(now - readingStamp[idx]) > (uint32_t)maxAgeMs || (now - readingStamp[idx]) > maxAgeMs) {
    readingSet[idx] = false;
    return false;
  }
  return true;
}

void expireStaleReadings() {
  if (veFrameSeen && currentVeFrameAgeMs() > (uint64_t)VE_STALE_MS) {
    veFrameStale = true;
  }
  for (int i = 0; i < VE_FIELD_COUNT; i++) {
    (void)readingFresh(i, VE_STALE_MS);
  }
  (void)readingFresh(R_COOLANT_INTAKE, TEMP_STALE_MS);
  (void)readingFresh(R_COOLANT_EXHAUST, TEMP_STALE_MS);
}

void appendGauge(String &out, const char *name, const char *help, int idx, unsigned long maxAgeMs, unsigned int decimals) {
  if (!readingFresh(idx, maxAgeMs)) {
    return;  // never measured, or stale: export nothing rather than a guess
  }
  out += "# HELP ";
  out += name;
  out += " ";
  out += help;
  out += "\n# TYPE ";
  out += name;
  out += " gauge\n";
  out += name;
  out += " ";
  out += String(readingValue[idx], decimals);
  out += "\n\n";
}

void handleMetrics() {
  expireStaleReadings();
  String out = "";
  out.reserve(1536);

  appendGauge(out, "sovereign_hw_battery_voltage_volts", "Battery voltage measured via VE.Direct", R_BATTERY_VOLTAGE, VE_STALE_MS, 2);
  appendGauge(out, "sovereign_hw_battery_current_amperes", "Battery current via VE.Direct (+ charging, - discharging)", R_BATTERY_CURRENT, VE_STALE_MS, 2);
  appendGauge(out, "sovereign_hw_solar_power_watts", "Instantaneous solar PV power (MPPT only)", R_SOLAR_POWER, VE_STALE_MS, 1);
  appendGauge(out, "sovereign_hw_battery_soc_percent", "Battery state of charge (battery monitor only)", R_BATTERY_SOC, VE_STALE_MS, 1);
  appendGauge(out, "sovereign_hw_coolant_intake_temp_celsius", "Coolant loop intake temperature", R_COOLANT_INTAKE, TEMP_STALE_MS, 2);
  appendGauge(out, "sovereign_hw_coolant_exhaust_temp_celsius", "Radiator exhaust temperature", R_COOLANT_EXHAUST, TEMP_STALE_MS, 2);

  if (veFrameSeen) {
    uint64_t ageMs = currentVeFrameAgeMs();
    out += "# HELP sovereign_hw_vedirect_last_frame_age_seconds Seconds since the last checksum-valid VE.Direct frame\n";
    out += "# TYPE sovereign_hw_vedirect_last_frame_age_seconds gauge\n";
    out += "sovereign_hw_vedirect_last_frame_age_seconds " + String((double)ageMs / 1000.0, 1) + "\n\n";
  }

  out += "# HELP sovereign_hw_vedirect_frames_valid_total Checksum-valid VE.Direct frames since boot\n";
  out += "# TYPE sovereign_hw_vedirect_frames_valid_total counter\n";
  out += "sovereign_hw_vedirect_frames_valid_total " + String(veFramesValid) + "\n\n";

  out += "# HELP sovereign_hw_vedirect_checksum_errors_total VE.Direct frames dropped for a bad checksum since boot\n";
  out += "# TYPE sovereign_hw_vedirect_checksum_errors_total counter\n";
  out += "sovereign_hw_vedirect_checksum_errors_total " + String(veChecksumErrors) + "\n";

  server.send(200, "text/plain; version=0.0.4", out);
}

void logReading(const char *label, int idx, unsigned long maxAgeMs, unsigned int decimals) {
  Serial.print(label);
  if (readingFresh(idx, maxAgeMs)) {
    Serial.print(String(readingValue[idx], decimals));
  } else {
    Serial.print("n/a");
  }
}

void logStatus() {
  expireStaleReadings();
  logReading("[STATUS] BATT V: ", R_BATTERY_VOLTAGE, VE_STALE_MS, 2);
  logReading(" | I A: ", R_BATTERY_CURRENT, VE_STALE_MS, 2);
  logReading(" | SoC %: ", R_BATTERY_SOC, VE_STALE_MS, 1);
  logReading(" | SOLAR W: ", R_SOLAR_POWER, VE_STALE_MS, 0);
  logReading(" | COOLANT IN C: ", R_COOLANT_INTAKE, TEMP_STALE_MS, 1);
  logReading(" | OUT C: ", R_COOLANT_EXHAUST, TEMP_STALE_MS, 1);
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, HIGH);

  veClearStaged();
  veSerial.begin(19200, SERIAL_8N1, RX_VE_DIRECT, TX_VE_DIRECT);

  sensors.begin();
  sensors.setWaitForConversion(false);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  server.on("/metrics", handleMetrics);
  server.on("/health", []() { server.send(200, "text/plain", "OK"); });
  server.begin();
  Serial.println("[ESP32] Sovereign Telemetry Bridge listening on :80/metrics");
}

void loop() {
  server.handleClient();

  // Drain whatever the UART has buffered; read() never waits for more data.
  while (veSerial.available() > 0) {
    int b = veSerial.read();
    if (b < 0) {
      break;
    }
    veHandleByte((uint8_t)b);
  }

  serviceTemperatures();
  expireStaleReadings();

  if ((uint32_t)(millis() - lastStatusLog) > (uint32_t)STATUS_LOG_MS) {
    lastStatusLog = millis();
    logStatus();
  }
  delay(2);
}
