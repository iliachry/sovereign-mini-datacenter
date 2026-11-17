// Host-side tests for firmware/esp32_telemetry_bridge.ino.
//
// The sketch is compiled UNMODIFIED: this file includes the stub Arduino core
// from stubs/arduino/ and then the .ino itself, the way the Arduino builder
// would (it prepends `#include <Arduino.h>` and prototypes; the sketch defines
// every function before its first use, so no prototypes are needed here).
//
// Build + run: tests/firmware/run.sh
#include <Arduino.h>

#ifndef SMDC_FIRMWARE_INO
#error "SMDC_FIRMWARE_INO must be the quoted path of the sketch under test (run.sh sets it)"
#endif
#include SMDC_FIRMWARE_INO

#include "ve_shared.h"

using vt::Bytes;

namespace {

std::string metrics_body() {
  WebServer::Response r = server.testGet("/metrics");
  CHECK(r.sent);
  CHECK(r.code == 200);
  CHECK(r.contentType == "text/plain; version=0.0.4");
  return r.body;
}

// The sample line `<name> <value>` of a metric, if exported.
std::optional<std::string> metric_text(const std::string &body, const std::string &name) {
  const std::string key = name + " ";
  size_t pos = 0;
  while (pos < body.size()) {
    size_t eol = body.find('\n', pos);
    if (eol == std::string::npos) {
      eol = body.size();
    }
    if (body.compare(pos, key.size(), key) == 0) {
      return body.substr(pos + key.size(), eol - pos - key.size());
    }
    pos = eol + 1;
  }
  return std::nullopt;
}

std::optional<double> metric_value(const std::string &name) {
  const std::optional<std::string> text = metric_text(metrics_body(), name);
  if (!text) {
    return std::nullopt;
  }
  char *end = nullptr;
  const double v = strtod(text->c_str(), &end);
  CHECK(end != text->c_str() && *end == '\0');
  return v;
}

bool exported(const std::string &name) { return metric_text(metrics_body(), name).has_value(); }

const char *const M_V = "sovereign_hw_battery_voltage_volts";
const char *const M_I = "sovereign_hw_battery_current_amperes";
const char *const M_PPV = "sovereign_hw_solar_power_watts";
const char *const M_SOC = "sovereign_hw_battery_soc_percent";
const char *const M_T_IN = "sovereign_hw_coolant_intake_temp_celsius";
const char *const M_T_OUT = "sovereign_hw_coolant_exhaust_temp_celsius";
const char *const M_AGE = "sovereign_hw_vedirect_last_frame_age_seconds";
const char *const M_VALID = "sovereign_hw_vedirect_frames_valid_total";
const char *const M_BAD = "sovereign_hw_vedirect_checksum_errors_total";

// Everything the shared tests observe goes through /metrics - the firmware's
// real output - and is cross-checked against its internal state.
struct ArduinoFlavour {
  static void boot() { setup(); }
  static void rx(const uint8_t *data, size_t len) { veSerial.testFeed(data, len); }
  static void service() { loop(); }
  static size_t pending() { return veSerial.testPending(); }
  static unsigned long valid_frames() {
    const std::optional<double> v = metric_value(M_VALID);
    CHECK(v.has_value());
    CHECK((unsigned long)*v == veFramesValid);
    return veFramesValid;
  }
  static unsigned long bad_frames() {
    const std::optional<double> v = metric_value(M_BAD);
    CHECK(v.has_value());
    CHECK((unsigned long)*v == veChecksumErrors);
    return veChecksumErrors;
  }
  static std::optional<double> reading(const char *metric, int idx) {
    const std::optional<double> v = metric_value(metric);
    CHECK(v.has_value() == readingFresh(idx, VE_STALE_MS));
    return v;
  }
  static std::optional<double> volts() { return reading(M_V, R_BATTERY_VOLTAGE); }
  static std::optional<double> amps() { return reading(M_I, R_BATTERY_CURRENT); }
  static std::optional<double> solar_watts() { return reading(M_PPV, R_SOLAR_POWER); }
  static std::optional<double> soc_percent() { return reading(M_SOC, R_BATTERY_SOC); }
  static double tolerance() { return 0.0051; }  // /metrics prints 2 decimals
  static void advance_ms(unsigned long ms) {
    smdc_host::now_ms += ms;  // freshness is evaluated when /metrics is rendered
    loop();
  }
};

void feed(const Bytes &b) { veSerial.testFeed(b.data(), b.size()); }

// Runs loop() until the test clock reaches `target_ms` (each pass costs the
// sketch's own delay(2)).
void run_until(unsigned long target_ms) {
  while (millis() < target_ms) {
    loop();
  }
}

void register_arduino_tests() {
  vt::add_test("setup() wires UART1 19200 8N1 on GPIO16/17, non-blocking DS18B20, :80 routes", [] {
    setup();
    CHECK(veSerial.testUartNr() == 1);
    CHECK(veSerial.testBegun());
    CHECK(veSerial.testBaud() == 19200);
    CHECK(veSerial.testConfig() == (uint32_t)SERIAL_8N1);
    CHECK(veSerial.testRxPin() == 16);
    CHECK(veSerial.testTxPin() == 17);
    CHECK(Serial.testBegun() && Serial.testBaud() == 115200);
    CHECK(oneWire.testPin == 4);
    CHECK(sensors.testBus() == &oneWire);
    CHECK(sensors.testBegun());
    CHECK(!sensors.testWaitForConversion());
    CHECK(WiFi.testMode == WIFI_STA);
    CHECK(WiFi.testBeginCalls == 1);
    CHECK(!WiFi.testSsid.empty());
    CHECK(server.testPort() == 80);
    CHECK(server.testBegun());
    WebServer::Response health = server.testGet("/health");
    CHECK(health.code == 200);
    CHECK(health.body == "OK");
    CHECK(server.testGet("/nope").code == 404);
  });

  vt::add_test("/metrics before any frame: counters only, no readings, no age gauge", [] {
    setup();
    loop();
    CHECK_EQ_STR(metrics_body(),
                 "# HELP sovereign_hw_vedirect_frames_valid_total Checksum-valid VE.Direct frames since boot\n"
                 "# TYPE sovereign_hw_vedirect_frames_valid_total counter\n"
                 "sovereign_hw_vedirect_frames_valid_total 0\n"
                 "\n"
                 "# HELP sovereign_hw_vedirect_checksum_errors_total VE.Direct frames dropped for a bad checksum "
                 "since boot\n"
                 "# TYPE sovereign_hw_vedirect_checksum_errors_total counter\n"
                 "sovereign_hw_vedirect_checksum_errors_total 0\n");
  });

  vt::add_test("/metrics after a battery-monitor frame: exact Prometheus text", [] {
    setup();
    feed(vt::shunt_frame(26412, -1530, 876));
    loop();
    smdc_host::now_ms += 1498;  // 1.5 s after the frame (loop() itself took 2 ms)
    CHECK_EQ_STR(metrics_body(),
                 "# HELP sovereign_hw_battery_voltage_volts Battery voltage measured via VE.Direct\n"
                 "# TYPE sovereign_hw_battery_voltage_volts gauge\n"
                 "sovereign_hw_battery_voltage_volts 26.41\n"
                 "\n"
                 "# HELP sovereign_hw_battery_current_amperes Battery current via VE.Direct (+ charging, - "
                 "discharging)\n"
                 "# TYPE sovereign_hw_battery_current_amperes gauge\n"
                 "sovereign_hw_battery_current_amperes -1.53\n"
                 "\n"
                 "# HELP sovereign_hw_battery_soc_percent Battery state of charge (battery monitor only)\n"
                 "# TYPE sovereign_hw_battery_soc_percent gauge\n"
                 "sovereign_hw_battery_soc_percent 87.6\n"
                 "\n"
                 "# HELP sovereign_hw_vedirect_last_frame_age_seconds Seconds since the last checksum-valid "
                 "VE.Direct frame\n"
                 "# TYPE sovereign_hw_vedirect_last_frame_age_seconds gauge\n"
                 "sovereign_hw_vedirect_last_frame_age_seconds 1.5\n"
                 "\n"
                 "# HELP sovereign_hw_vedirect_frames_valid_total Checksum-valid VE.Direct frames since boot\n"
                 "# TYPE sovereign_hw_vedirect_frames_valid_total counter\n"
                 "sovereign_hw_vedirect_frames_valid_total 1\n"
                 "\n"
                 "# HELP sovereign_hw_vedirect_checksum_errors_total VE.Direct frames dropped for a bad checksum "
                 "since boot\n"
                 "# TYPE sovereign_hw_vedirect_checksum_errors_total counter\n"
                 "sovereign_hw_vedirect_checksum_errors_total 0\n");
  });

  vt::add_test("/metrics after an MPPT frame: PPV in watts, no SOC line at all", [] {
    setup();
    feed(vt::mppt_frame(27540, 12300, 345, 78120, 187));
    loop();
    const std::string body = metrics_body();
    CHECK_EQ_STR(metric_text(body, M_V).value_or("<absent>"), "27.54");
    CHECK_EQ_STR(metric_text(body, M_I).value_or("<absent>"), "12.30");
    CHECK_EQ_STR(metric_text(body, M_PPV).value_or("<absent>"), "345.0");
    CHECK(body.find("soc") == std::string::npos);  // not even HELP/TYPE
    CHECK(!readingSet[R_BATTERY_SOC]);
  });

  vt::add_test("/metrics for the Victron FAQ frame: 26.20 V, 0.00 A, 100.0 %", [] {
    setup();
    feed(vt::victron_faq_bmv700_frame());
    loop();
    const std::string body = metrics_body();
    CHECK_EQ_STR(metric_text(body, M_V).value_or("<absent>"), "26.20");
    CHECK_EQ_STR(metric_text(body, M_I).value_or("<absent>"), "0.00");
    CHECK_EQ_STR(metric_text(body, M_SOC).value_or("<absent>"), "100.0");
    CHECK(!metric_text(body, M_PPV).has_value());
  });

  vt::add_test("rejected frame: only the checksum-error counter moves in /metrics", [] {
    setup();
    Bytes f = vt::shunt_frame(26412, -1530, 876);
    f[f.size() / 2] ^= 0x10;
    feed(f);
    loop();
    const std::string body = metrics_body();
    CHECK_EQ_STR(metric_text(body, M_BAD).value_or("<absent>"), "1");
    CHECK_EQ_STR(metric_text(body, M_VALID).value_or("<absent>"), "0");
    CHECK(body.find(" gauge\n") == std::string::npos);  // no reading, no age gauge
    for (int i = 0; i < READING_COUNT; i++) {
      CHECK(!readingSet[i]);
    }
    CHECK(!veFrameSeen);
  });

  vt::add_test("parser never blocks: a partial frame returns at once, the clock does not move", [] {
    setup();
    const Bytes f = vt::shunt_frame(26412, -1530, 876);
    const size_t half = f.size() / 2;

    // 1) Straight into the byte handler: no time may pass at all.
    const unsigned long t0 = millis();
    const unsigned long delays0 = smdc_host::delay_calls;
    for (size_t i = 0; i < half; i++) {
      veHandleByte(f[i]);
    }
    CHECK(millis() == t0);
    CHECK(smdc_host::delay_calls == delays0);
    CHECK(veFramesValid == 0 && veChecksumErrors == 0);

    // 2) Through loop(): drains the UART, then yields with its own delay(2).
    //    A parser that waited for the rest of the frame (readBytesUntil with
    //    a 1 s timeout, say) would show up as extra elapsed time.
    veSerial.testFeed(f.data() + half, 10);
    const unsigned long t1 = millis();
    const unsigned long handled1 = server.testHandleClientCalls();
    loop();
    CHECK(veSerial.testPending() == 0);
    CHECK(millis() - t1 == 2);
    CHECK(smdc_host::delay_calls == delays0 + 1);
    CHECK(server.testHandleClientCalls() == handled1 + 1);  // HTTP stays served
    CHECK(veFramesValid == 0);
    CHECK(!exported(M_V));

    // 3) Many loop passes with an idle UART: still exactly 2 ms each.
    const unsigned long t2 = millis();
    for (int i = 0; i < 500; i++) {
      loop();
    }
    CHECK(millis() - t2 == 1000);
    CHECK(veFramesValid == 0);

    // 4) The rest arrives later and completes the SAME frame.
    veSerial.testFeed(f.data() + half + 10, f.size() - half - 10);
    loop();
    CHECK(veFramesValid == 1 && veChecksumErrors == 0);
    CHECK_EQ_STR(metric_text(metrics_body(), M_V).value_or("<absent>"), "26.41");
    CHECK(veSerial.testBytesRead() == f.size() - half);  // loop() read each byte exactly once
  });

  vt::add_test("staleness: readings withdrawn 10 s after the last valid frame, restored by the next", [] {
    setup();
    CHECK(!exported(M_AGE));  // no frame yet: no age gauge

    smdc_host::now_ms = 5000;
    feed(vt::shunt_frame(26412, -1530, 876));
    loop();  // frame committed at t = 5000, loop ends at 5002
    CHECK(veLastFrameMs == 5000);
    CHECK_EQ_STR(metric_text(metrics_body(), M_AGE).value_or("<absent>"), "0.0");

    smdc_host::now_ms = 5000 + 9999;
    CHECK(exported(M_V) && exported(M_I) && exported(M_SOC));
    CHECK_EQ_STR(metric_text(metrics_body(), M_AGE).value_or("<absent>"), "10.0");

    smdc_host::now_ms = 5000 + VE_STALE_MS;  // exactly at the limit: still valid
    CHECK(exported(M_V) && exported(M_I) && exported(M_SOC));

    smdc_host::now_ms = 5000 + VE_STALE_MS + 1;  // past the limit: withdrawn
    CHECK(!exported(M_V) && !exported(M_I) && !exported(M_SOC) && !exported(M_PPV));
    // The age gauge and the counters stay, so the outage is visible.
    CHECK_EQ_STR(metric_text(metrics_body(), M_AGE).value_or("<absent>"), "10.0");
    CHECK_EQ_STR(metric_text(metrics_body(), M_VALID).value_or("<absent>"), "1");

    smdc_host::now_ms = 5000 + 125000;
    CHECK(!exported(M_V));
    CHECK_EQ_STR(metric_text(metrics_body(), M_AGE).value_or("<absent>"), "125.0");

    // Full 32-bit millis() rollover without a new frame must never revive stale readings or wrap age.
    smdc_host::now_ms = (1ULL << 32) + 6000;  // uint32_t difference wraps back to 1000 ms
    CHECK(!exported(M_V) && !exported(M_I) && !exported(M_SOC) && !exported(M_PPV));
    CHECK(metric_value(M_AGE).value_or(0.0) > (double)VE_STALE_MS / 1000.0);
    smdc_host::now_ms = 6000;  // 32-bit ESP32 millis() value after rollover
    CHECK(!exported(M_V) && !exported(M_I) && !exported(M_SOC) && !exported(M_PPV));
    CHECK(metric_value(M_AGE).value_or(0.0) > (double)VE_STALE_MS / 1000.0);
    smdc_host::now_ms = 5000 + 125000;

    // Rejected frames do not revive anything.
    Bytes bad = vt::shunt_frame(25990, 4200, 431);
    bad.back() ^= 0x01;
    feed(bad);
    loop();
    CHECK(veChecksumErrors == 1);
    CHECK(!exported(M_V) && !exported(M_SOC));

    // The next valid frame does.
    const unsigned long t = millis();
    feed(vt::shunt_frame(25990, 4200, 431));
    loop();
    CHECK(veLastFrameMs == t);
    const std::string body = metrics_body();
    CHECK_EQ_STR(metric_text(body, M_V).value_or("<absent>"), "25.99");
    CHECK_EQ_STR(metric_text(body, M_I).value_or("<absent>"), "4.20");
    CHECK_EQ_STR(metric_text(body, M_SOC).value_or("<absent>"), "43.1");
    CHECK_EQ_STR(metric_text(body, M_AGE).value_or("<absent>"), "0.0");
    CHECK_EQ_STR(metric_text(body, M_VALID).value_or("<absent>"), "2");
  });

  vt::add_test("staleness is per reading: a field the frames stop carrying is withdrawn on its own", [] {
    setup();
    smdc_host::now_ms = 1000;
    feed(vt::shunt_frame(26412, -1530, 876));
    loop();
    // Frames keep coming, but without SOC (e.g. the shunt is swapped for an MPPT).
    for (unsigned long t = 2000; t <= 12000; t += 1000) {
      smdc_host::now_ms = t;
      feed(vt::mppt_frame(27540, 12300, 345, 78120, 187));
      loop();
    }
    CHECK(exported(M_V) && exported(M_I) && exported(M_PPV));
    CHECK(!exported(M_SOC));  // last SOC is 11 s old
    CHECK_EQ_STR(metric_text(metrics_body(), M_AGE).value_or("<absent>"), "0.0");
  });

  vt::add_test("DS18B20: -127 (no probe) is not exported, a real temperature is, in the background", [] {
    setup();
    sensors.testSetProbe(0, 21.5f);  // intake answers
    // probe 1 (exhaust) is not connected -> library returns -127

    run_until(1998);
    CHECK(sensors.testRequests() == 0);  // first conversion starts at TEMP_PERIOD_MS
    run_until(2000 + 2);
    CHECK(sensors.testRequests() == 1);
    CHECK(sensors.testReads() == 0);  // not collected before the conversion time
    CHECK(!exported(M_T_IN));
    run_until(2000 + TEMP_CONVERSION_MS + 2);
    CHECK(sensors.testReads() == 2);

    std::string body = metrics_body();
    CHECK_EQ_STR(metric_text(body, M_T_IN).value_or("<absent>"), "21.50");
    CHECK(!metric_text(body, M_T_OUT).has_value());
    CHECK(body.find("-127") == std::string::npos);
    CHECK(!readingSet[R_COOLANT_EXHAUST]);

    // The exhaust probe appears; a sub-zero reading is a temperature too.
    sensors.testSetProbe(0, -3.25f);
    sensors.testSetProbe(1, 34.25f);
    run_until(millis() + TEMP_PERIOD_MS + TEMP_CONVERSION_MS + 10);
    body = metrics_body();
    CHECK_EQ_STR(metric_text(body, M_T_IN).value_or("<absent>"), "-3.25");
    CHECK_EQ_STR(metric_text(body, M_T_OUT).value_or("<absent>"), "34.25");

    // The intake probe drops off the bus: its last value is kept only until
    // it is TEMP_STALE_MS old, then withdrawn; -127 never reaches /metrics.
    const unsigned long last_good = readingStamp[R_COOLANT_INTAKE];
    sensors.testDisconnectProbe(0);
    run_until(last_good + TEMP_STALE_MS - 100);
    CHECK_EQ_STR(metric_text(metrics_body(), M_T_IN).value_or("<absent>"), "-3.25");
    run_until(last_good + TEMP_STALE_MS + 10);
    body = metrics_body();
    CHECK(!metric_text(body, M_T_IN).has_value());
    CHECK(metric_text(body, M_T_OUT).has_value());
    CHECK(body.find("-127") == std::string::npos);

    // Nothing ever waited on the sensor: all elapsed time is loop()'s delay(2).
    CHECK(smdc_host::delayed_ms == 2 * smdc_host::delay_calls);
    CHECK(smdc_host::now_ms == smdc_host::delayed_ms);
  });

  vt::add_test("USB status log prints n/a for readings that are not available", [] {
    setup();
    run_until(STATUS_LOG_MS + 10);
    CHECK(Serial.testOutput().find("[STATUS] BATT V: n/a | I A: n/a | SoC %: n/a | SOLAR W: n/a | "
                                   "COOLANT IN C: n/a | OUT C: n/a\r\n") != std::string::npos);
    feed(vt::shunt_frame(26412, -1530, 876));
    run_until(2 * STATUS_LOG_MS + 20);
    CHECK(Serial.testOutput().find("[STATUS] BATT V: 26.41 | I A: -1.53 | SoC %: 87.6 | SOLAR W: n/a") !=
          std::string::npos);
  });
}

}  // namespace

int main(int argc, char **argv) {
  vt::register_shared_tests<ArduinoFlavour>();
  register_arduino_tests();
  return vt::run_all("arduino", argc, argv);
}
