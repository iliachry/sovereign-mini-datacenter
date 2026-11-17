// Host-side tests for the VE.Direct lambdas of firmware/esphome_smdc_bridge.yaml.
//
// run.sh extracts the lambda text from the YAML (extract_esphome_lambdas.py)
// into esphome_lambdas.generated.h; this file compiles that text against
// stubs/esphome/esphome_stubs.h and runs the same shared corpus as the Arduino
// suite (ve_shared.h), plus the ESPHome-only behaviour (extra fields, the
// staleness watchdog, the frame-age sensor).
#include "ve_shared.h"

#include "esphome_stubs.h"

#include "esphome_lambdas.generated.h"

using vt::Bytes;

namespace {

unsigned long count_containing(const std::vector<std::string> &log, const char *needle) {
  unsigned long n = 0;
  for (const std::string &line : log) {
    if (line.find(needle) != std::string::npos) {
      n++;
    }
  }
  return n;
}

std::optional<double> reported(const StubSensor &s) {
  if (!s.reporting()) {
    return std::nullopt;
  }
  return (double)s.state;
}

struct EsphomeFlavour {
  static void boot() {}
  static void rx(const uint8_t *data, size_t len) {
    uart_ve_direct.rx.insert(uart_ve_direct.rx.end(), data, data + len);
  }
  static void service() {
    smdc_ve_parser_lambda();  // the `interval: 100ms` body
    CHECK(uart_ve_direct.blocking_reads == 0);
  }
  static size_t pending() { return uart_ve_direct.rx.size(); }
  static unsigned long valid_frames() { return ve_last_valid_ms.writes; }
  static unsigned long bad_frames() { return count_containing(smdc_host::warnings, "bad checksum"); }
  static std::optional<double> volts() { return reported(battery_voltage); }
  static std::optional<double> amps() { return reported(battery_current); }
  static std::optional<double> solar_watts() { return reported(solar_power); }
  static std::optional<double> soc_percent() { return reported(battery_soc); }
  static double tolerance() { return 1e-4; }
  static void advance_ms(unsigned long ms) {
    smdc_host::now_ms += (uint32_t)ms;
    smdc_ve_watchdog_lambda();  // the `interval: 1s` body
  }
};

void feed(const Bytes &b) {
  EsphomeFlavour::rx(b.data(), b.size());
  EsphomeFlavour::service();
}

bool close_to(float got, double want) { return std::fabs((double)got - want) <= 1e-3; }

const StubSensor *const kAll[] = {&battery_voltage, &battery_current, &solar_power,
                                  &battery_soc,     &solar_voltage,   &solar_yield_today};

void register_esphome_tests() {
  vt::add_test("MPPT extras: VPV mV->V and H20 0.01 kWh->Wh; a battery monitor leaves them unknown", [] {
    feed(vt::shunt_frame(26412, -1530, 876));
    CHECK(!solar_voltage.has_state && !solar_yield_today.has_state && !solar_power.has_state);
    feed(vt::mppt_frame(27540, 12300, 345, 78120, 187));
    CHECK(close_to(solar_voltage.state, 78.120));
    CHECK(close_to(solar_yield_today.state, 1870.0));  // "H19 1234"/"H21 410" are not H20
    CHECK(close_to(solar_power.state, 345.0));
    CHECK(ve_frame_seen);
  });

  vt::add_test("a rejected frame logs a warning and publishes to no sensor", [] {
    Bytes f = vt::mppt_frame(27540, 12300, 345, 78120, 187);
    f[f.size() / 2] ^= 0x10;
    feed(f);
    CHECK(count_containing(smdc_host::warnings, "bad checksum") == 1);
    for (const StubSensor *s : kAll) {
      CHECK(s->publishes == 0);
    }
    CHECK(!ve_frame_seen);
    CHECK(ve_last_valid_ms.writes == 0);
  });

  vt::add_test("parser never blocks: read_byte() is only called when a byte is available", [] {
    const Bytes f = vt::shunt_frame(26412, -1530, 876);
    smdc_ve_parser_lambda();  // empty UART
    smdc_ve_parser_lambda();
    CHECK(uart_ve_direct.blocking_reads == 0 && uart_ve_direct.bytes_read == 0);
    EsphomeFlavour::rx(f.data(), f.size() / 2);
    smdc_ve_parser_lambda();  // half a frame: drained, nothing published, no waiting
    CHECK(uart_ve_direct.rx.empty());
    CHECK(uart_ve_direct.blocking_reads == 0);
    CHECK(battery_voltage.publishes == 0);
    smdc_ve_parser_lambda();
    EsphomeFlavour::rx(f.data() + f.size() / 2, f.size() - f.size() / 2);
    smdc_ve_parser_lambda();
    CHECK(uart_ve_direct.blocking_reads == 0);
    CHECK(uart_ve_direct.bytes_read == f.size());
    CHECK(close_to(battery_voltage.state, 26.412));
  });

  vt::add_test("watchdog: NaN to all six sensors after 10 s without a valid frame, restored by the next", [] {
    smdc_host::now_ms = 60000;
    smdc_ve_watchdog_lambda();  // no frame ever seen: nothing to withdraw
    for (const StubSensor *s : kAll) {
      CHECK(s->publishes == 0);
    }
    CHECK(!ve_stale);

    feed(vt::mppt_frame(27540, 12300, 345, 78120, 187));
    CHECK(ve_frame_seen && ve_last_valid_ms.value == 60000);

    smdc_host::now_ms = 60000 + 9999;
    smdc_ve_watchdog_lambda();
    CHECK(!ve_stale);
    CHECK(battery_voltage.reporting() && solar_power.reporting());

    smdc_host::now_ms = 60000 + 10000;
    smdc_ve_watchdog_lambda();
    CHECK(ve_stale);
    CHECK(count_containing(smdc_host::warnings, "No valid VE.Direct frame") == 1);
    for (const StubSensor *s : kAll) {
      CHECK(s->has_state && std::isnan(s->state));  // published as NaN = unknown
      CHECK(!s->reporting());
    }

    // Further watchdog passes do not publish (or log) again.
    const unsigned long publishes = battery_voltage.publishes;
    smdc_host::now_ms += 30000;
    smdc_ve_watchdog_lambda();
    smdc_ve_watchdog_lambda();
    CHECK(battery_voltage.publishes == publishes);
    CHECK(count_containing(smdc_host::warnings, "No valid VE.Direct frame") == 1);

    // A rejected frame does not lift the withdrawal.
    Bytes bad = vt::mppt_frame(27000, 1000, 100, 70000, 190);
    bad.back() ^= 0x01;
    feed(bad);
    CHECK(ve_stale && !battery_voltage.reporting());

    // A valid one does.
    feed(vt::mppt_frame(27000, 1000, 100, 70000, 190));
    CHECK(!ve_stale);
    CHECK(count_containing(smdc_host::infos, "frames resumed") == 1);
    CHECK(close_to(battery_voltage.state, 27.0));
    CHECK(close_to(solar_power.state, 100.0));
    CHECK(close_to(solar_yield_today.state, 1900.0));
    CHECK(!battery_soc.reporting());  // an MPPT never revives SOC
    smdc_ve_watchdog_lambda();
    CHECK(!ve_stale && battery_voltage.reporting());
  });

  vt::add_test("watchdog: one field expires on its own, with one NaN publish and one warning naming it", [] {
    smdc_host::now_ms = 1000;
    feed(vt::shunt_frame(26412, -1530, 900));
    CHECK(ve_field_live[0] && ve_field_live[1] && ve_field_live[3]);
    CHECK(!ve_field_live[2] && !ve_field_live[4] && !ve_field_live[5]);  // a shunt sends no PPV/VPV/H20
    const unsigned long soc_publishes = battery_soc.publishes;
    for (int second = 1; second <= 61; second++) {
      smdc_host::now_ms += 1000;
      feed(vt::ve_frame({{"V", "26000"}, {"I", "-1530"}, {"SOC", "---"}}));
      smdc_ve_watchdog_lambda();
    }
    CHECK(!ve_stale);  // frames never stopped: this is not the all-sensors withdrawal
    CHECK(battery_soc.has_state && std::isnan(battery_soc.state));
    CHECK(battery_soc.publishes == soc_publishes + 1);  // withdrawn once, not every second
    CHECK(!ve_field_live[3]);
    CHECK(count_containing(smdc_host::warnings, "Field SOC missing or unusable") == 1);
    CHECK(count_containing(smdc_host::warnings, "No valid VE.Direct frame") == 0);
    CHECK(battery_voltage.reporting() && battery_current.reporting());
    // Sensors the device never provided are left untouched (still "unknown").
    CHECK(solar_power.publishes == 0 && solar_voltage.publishes == 0 && solar_yield_today.publishes == 0);
    CHECK(close_to(smdc_ve_frame_age_lambda(), 0.0));  // the frame age is about frames, not fields
  });

  vt::add_test("frame-age sensor: unknown before the first frame, keeps counting while withdrawn", [] {
    smdc_host::now_ms = 1000;
    CHECK(std::isnan(smdc_ve_frame_age_lambda()));
    feed(vt::shunt_frame(26412, -1530, 876));
    CHECK(close_to(smdc_ve_frame_age_lambda(), 0.0));
    smdc_host::now_ms = 1000 + 7500;
    CHECK(close_to(smdc_ve_frame_age_lambda(), 7.5));
    smdc_host::now_ms = 1000 + 90000;
    smdc_ve_watchdog_lambda();
    CHECK(ve_stale);
    CHECK(close_to(smdc_ve_frame_age_lambda(), 90.0));
  });

  vt::add_test("staleness survives the 49.7-day millis() wrap", [] {
    smdc_host::now_ms = 0xFFFFFF00u;
    feed(vt::shunt_frame(26412, -1530, 876));
    smdc_host::now_ms = 0x00000100u;  // 512 ms later, after the counter wrapped
    smdc_ve_watchdog_lambda();
    CHECK(!ve_stale);
    CHECK(close_to(smdc_ve_frame_age_lambda(), 0.512));
    smdc_host::now_ms = 0xFFFFFF00u + 10000u;  // exact 10,000 ms threshold after frame at 0xFFFFFF00u
    smdc_ve_watchdog_lambda();
    CHECK(ve_stale);
    CHECK(close_to(smdc_ve_frame_age_lambda(), 10.0));  // exact 10.0 s, not a false rollover
    smdc_host::now_ms = 0xFFFFFF00u + 10001u;
    CHECK(close_to(smdc_ve_frame_age_lambda(), 10.001));
    // After a full 32-bit wrap while remaining stale, age stays > 4e6 s at both +1s and +11s wrapped diff.
    smdc_host::now_ms = 0xFFFFFF00u + 1000u;
    smdc_ve_watchdog_lambda();
    CHECK(ve_stale);
    CHECK(!battery_voltage.reporting() && !battery_soc.reporting());
    CHECK(smdc_ve_frame_age_lambda() > 4000000.0);
    smdc_host::now_ms = 0xFFFFFF00u + 11000u;
    smdc_ve_watchdog_lambda();
    CHECK(smdc_ve_frame_age_lambda() > 4000000.0);
  });
}

}  // namespace

int main(int argc, char **argv) {
  vt::register_shared_tests<EsphomeFlavour>();
  register_esphome_tests();
  return vt::run_all("esphome", argc, argv);
}
