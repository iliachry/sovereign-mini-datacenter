"""Regression tests for the firmware -> exporter -> HAL path (tri-council round 3, finding 8).

A fake ESP32 bridge (a loopback HTTP server) serves the two firmware flavours' ``/metrics``
formats; the power exporter (packaged and standalone copy) must re-export them under the
canonical ``sovereign_*`` names, and the HAL and the sentinel must then get measured readings.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from sovereign_dc import telemetry
from sovereign_dc.agents import sentinel_copilot
from sovereign_dc.hal.power import TelemetryUnavailable, read_power
from sovereign_dc.hal.thermal import read_thermal

ROOT = Path(__file__).resolve().parent.parent
STANDALONE = ROOT / "software" / "telemetry" / "power_exporter.py"
PACKAGED = ROOT / "src" / "sovereign_dc" / "telemetry.py"

# Shape of the sketch's /metrics (see tests/firmware/test_arduino_bridge.cpp): a battery
# monitor on the VE.Direct port (no PPV) and both DS18B20 probes answering.
ARDUINO_SHUNT = """\
# HELP sovereign_hw_battery_voltage_volts Battery voltage measured via VE.Direct
# TYPE sovereign_hw_battery_voltage_volts gauge
sovereign_hw_battery_voltage_volts 26.41

# HELP sovereign_hw_battery_current_amperes Battery current via VE.Direct (+ charging, - discharging)
# TYPE sovereign_hw_battery_current_amperes gauge
sovereign_hw_battery_current_amperes -1.53

# HELP sovereign_hw_battery_soc_percent Battery state of charge (battery monitor only)
# TYPE sovereign_hw_battery_soc_percent gauge
sovereign_hw_battery_soc_percent 87.6

# HELP sovereign_hw_coolant_intake_temp_celsius Coolant loop intake temperature
# TYPE sovereign_hw_coolant_intake_temp_celsius gauge
sovereign_hw_coolant_intake_temp_celsius 31.25

# HELP sovereign_hw_coolant_exhaust_temp_celsius Radiator exhaust temperature
# TYPE sovereign_hw_coolant_exhaust_temp_celsius gauge
sovereign_hw_coolant_exhaust_temp_celsius 38.50

# HELP sovereign_hw_vedirect_last_frame_age_seconds Seconds since the last checksum-valid VE.Direct frame
# TYPE sovereign_hw_vedirect_last_frame_age_seconds gauge
sovereign_hw_vedirect_last_frame_age_seconds 1.5

# HELP sovereign_hw_vedirect_frames_valid_total Checksum-valid VE.Direct frames since boot
# TYPE sovereign_hw_vedirect_frames_valid_total counter
sovereign_hw_vedirect_frames_valid_total 1

# HELP sovereign_hw_vedirect_checksum_errors_total VE.Direct frames dropped for a bad checksum since boot
# TYPE sovereign_hw_vedirect_checksum_errors_total counter
sovereign_hw_vedirect_checksum_errors_total 0
"""

# An MPPT on the port: PPV but never SOC; no probe answered.
ARDUINO_MPPT = """\
sovereign_hw_battery_voltage_volts 52.10
sovereign_hw_battery_current_amperes 12.40
sovereign_hw_solar_power_watts 640.0
sovereign_hw_vedirect_last_frame_age_seconds 0.4
sovereign_hw_vedirect_frames_valid_total 911
sovereign_hw_vedirect_checksum_errors_total 2
"""

# What the firmware serves 25 s after the VE.Direct cable was pulled: it has withdrawn the
# VE.Direct gauges itself; the probes still answer.
ARDUINO_STALE = """\
sovereign_hw_coolant_intake_temp_celsius 30.00
sovereign_hw_coolant_exhaust_temp_celsius 36.00
sovereign_hw_vedirect_last_frame_age_seconds 25.0
sovereign_hw_vedirect_frames_valid_total 40
sovereign_hw_vedirect_checksum_errors_total 0
"""

# ESPHome's prometheus component (esphome/components/prometheus/prometheus_handler.cpp):
# one esphome_sensor_failed row per sensor and a value row only for a non-NaN state; labels
# id[,area][,node][,friendly_name],name[,unit]. Here the MPPT-only sensors are failed.
ESPHOME_SHUNT = """\
#TYPE esphome_sensor_value gauge
#TYPE esphome_sensor_failed gauge
esphome_sensor_failed{id="coolant_loop_intake_temp",node="sovereign-telemetry-bridge",name="Coolant Loop Intake Temp"} 0
esphome_sensor_value{id="coolant_loop_intake_temp",node="sovereign-telemetry-bridge",name="Coolant Loop Intake Temp",unit="°C"} 31.25
esphome_sensor_failed{id="radiator_exhaust_temp",node="sovereign-telemetry-bridge",name="Radiator Exhaust Temp"} 0
esphome_sensor_value{id="radiator_exhaust_temp",node="sovereign-telemetry-bridge",name="Radiator Exhaust Temp",unit="°C"} 38.50
esphome_sensor_failed{id="battery_voltage",node="sovereign-telemetry-bridge",name="Battery Voltage"} 0
esphome_sensor_value{id="battery_voltage",node="sovereign-telemetry-bridge",name="Battery Voltage",unit="V"} 26.41
esphome_sensor_failed{id="battery_current",node="sovereign-telemetry-bridge",name="Battery Current"} 0
esphome_sensor_value{id="battery_current",node="sovereign-telemetry-bridge",name="Battery Current",unit="A"} -1.53
esphome_sensor_failed{id="solar_harvest_power",node="sovereign-telemetry-bridge",name="Solar Harvest Power"} 1
esphome_sensor_failed{id="battery_soc",node="sovereign-telemetry-bridge",name="Battery SoC"} 0
esphome_sensor_value{id="battery_soc",node="sovereign-telemetry-bridge",name="Battery SoC",unit="%"} 87.6
esphome_sensor_failed{id="solar_array_voltage",node="sovereign-telemetry-bridge",name="Solar Array Voltage"} 1
esphome_sensor_failed{id="solar_yield_today",node="sovereign-telemetry-bridge",name="Solar Yield Today"} 1
esphome_sensor_failed{id="ve_direct_last_valid_frame_age",node="sovereign-telemetry-bridge",name="VE.Direct Last Valid Frame Age"} 0
esphome_sensor_value{id="ve_direct_last_valid_frame_age",node="sovereign-telemetry-bridge",name="VE.Direct Last Valid Frame Age",unit="s"} 1
esphome_sensor_failed{id="wifi_signal",node="sovereign-telemetry-bridge",name="WiFi Signal"} 0
esphome_sensor_value{id="wifi_signal",node="sovereign-telemetry-bridge",name="WiFi Signal",unit="dBm"} -61
esphome_sensor_failed{id="bridge_uptime",node="sovereign-telemetry-bridge",name="Bridge Uptime"} 0
esphome_sensor_value{id="bridge_uptime",node="sovereign-telemetry-bridge",name="Bridge Uptime",unit="s"} 5400
#TYPE esphome_binary_sensor_value gauge
esphome_binary_sensor_value{id="bridge_status",node="sovereign-telemetry-bridge",name="Bridge Status"} 1
"""

# An MPPT behind ESPHome, in the older label layout (id, name, unit only).
ESPHOME_MPPT = """\
esphome_sensor_value{id="battery_voltage",name="Battery Voltage",unit="V"} 52.10
esphome_sensor_value{id="solar_harvest_power",name="Solar Harvest Power",unit="W"} 640
esphome_sensor_value{id="solar_yield_today",name="Solar Yield Today",unit="Wh"} 2350
esphome_sensor_failed{id="battery_soc",name="Battery SoC"} 1
esphome_sensor_value{id="ve_direct_last_valid_frame_age",name="VE.Direct Last Valid Frame Age",unit="s"} 0
"""

SHUNT_CANONICAL = {
    "sovereign_telemetry_simulated": 0.0,
    "sovereign_telemetry_source_up": 1.0,
    "sovereign_battery_soc_percent": 87.6,
    "sovereign_battery_voltage_volts": 26.41,
    "sovereign_battery_current_amperes": -1.53,
    "sovereign_temp_coolant_celsius": 38.5,
    "sovereign_temp_coolant_intake_celsius": 31.25,
    "sovereign_temp_coolant_exhaust_celsius": 38.5,
    "sovereign_load_shedding_active": 0.0,
}


class FakeBridge:
    """A loopback HTTP server standing in for the ESP32 bridge's ``/metrics``."""

    def __init__(self) -> None:
        self.body = ""
        self.status = 200
        self.hits = 0
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                bridge.hits += 1
                payload = bridge.body.encode("utf-8")
                self.send_response(bridge.status)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/metrics"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeBridge]:
    fake = FakeBridge()
    monkeypatch.setenv("ESP32_BRIDGE_URL", fake.url)
    yield fake
    fake.close()


def _load_standalone(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("SIMULATE_POWER_DATA", "false")
    spec = importlib.util.spec_from_file_location("power_exporter_bridge_test", STANDALONE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["packaged", "standalone"])
def exporter(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The exporter in hardware mode: ``sovereign_dc.telemetry`` and the standalone copy."""
    if request.param == "standalone":
        return _load_standalone(monkeypatch)
    monkeypatch.setattr(telemetry, "SIMULATION", False)
    monkeypatch.setattr(telemetry, "_bridge_cache", None)
    monkeypatch.setattr(telemetry, "_bridge_failure_logged", None)
    return telemetry


def _series(text: str) -> dict[str, float]:
    return {ln.split()[0]: float(ln.split()[1]) for ln in text.splitlines() if ln and not ln.startswith("#")}


def _scrape(exporter: ModuleType) -> dict[str, float]:
    exporter._bridge_cache = None  # every scrape in these tests hits the fake bridge
    return _series(exporter.get_telemetry_metrics())


@pytest.mark.parametrize("body", [ARDUINO_SHUNT, ESPHOME_SHUNT], ids=["arduino", "esphome"])
def test_bridge_readings_are_exported_under_the_canonical_names(
    exporter: ModuleType, bridge: FakeBridge, body: str
) -> None:
    bridge.body = body
    series = _scrape(exporter)
    age = series.pop("sovereign_telemetry_bridge_frame_age_seconds")
    assert age in (1.0, 1.5)
    assert series == pytest.approx(SHUNT_CANONICAL)
    # Nothing the bridge cannot measure is invented.
    for absent in (
        "sovereign_solar_pv_power_watts",
        "sovereign_solar_daily_yield_kwh",
        "sovereign_system_power_draw_watts",
        "sovereign_temp_rack_inlet_celsius",
        "sovereign_temp_rack_exhaust_celsius",
    ):
        assert absent not in series


@pytest.mark.parametrize("body", [ARDUINO_MPPT, ESPHOME_MPPT], ids=["arduino", "esphome"])
def test_mppt_only_bridge_exports_no_soc_and_no_load_shedding_flag(
    exporter: ModuleType, bridge: FakeBridge, body: str
) -> None:
    bridge.body = body
    series = _scrape(exporter)
    assert series["sovereign_telemetry_source_up"] == 1.0
    assert series["sovereign_solar_pv_power_watts"] == 640.0
    assert series["sovereign_battery_voltage_volts"] == 52.1
    assert "sovereign_battery_soc_percent" not in series
    assert "sovereign_load_shedding_active" not in series  # never derived without a SoC
    assert "sovereign_temp_coolant_celsius" not in series
    if body is ESPHOME_MPPT:
        assert series["sovereign_solar_daily_yield_kwh"] == pytest.approx(2.35)  # 2350 Wh


def test_load_shedding_flag_follows_the_measured_soc(exporter: ModuleType, bridge: FakeBridge) -> None:
    bridge.body = "sovereign_hw_battery_soc_percent 19.9\nsovereign_hw_vedirect_last_frame_age_seconds 0.2\n"
    assert _scrape(exporter)["sovereign_load_shedding_active"] == 1.0
    bridge.body = "sovereign_hw_battery_soc_percent 20.0\nsovereign_hw_vedirect_last_frame_age_seconds 0.2\n"
    assert _scrape(exporter)["sovereign_load_shedding_active"] == 0.0


def test_stale_vedirect_frame_reports_the_source_down(
    exporter: ModuleType, bridge: FakeBridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge.body = ARDUINO_STALE
    series = _scrape(exporter)
    assert series["sovereign_telemetry_source_up"] == 0.0
    assert series["sovereign_telemetry_simulated"] == 0.0
    assert series["sovereign_temp_coolant_celsius"] == 36.0  # probes are still fresh
    assert "sovereign_battery_soc_percent" not in series

    # A bridge that still sends VE.Direct values with an old frame age: the values are withheld.
    bridge.body = ARDUINO_SHUNT.replace("frame_age_seconds 1.5", "frame_age_seconds 10.5")
    series = _scrape(exporter)
    assert series["sovereign_telemetry_source_up"] == 0.0
    assert not any(name.startswith("sovereign_battery_") for name in series)
    assert "sovereign_load_shedding_active" not in series

    monkeypatch.setenv("ESP32_BRIDGE_MAX_FRAME_AGE_SECONDS", "30")
    series = _scrape(exporter)
    assert series["sovereign_telemetry_source_up"] == 1.0
    assert series["sovereign_battery_soc_percent"] == 87.6


@pytest.mark.parametrize("case", ["unreachable", "http_500", "empty", "unrelated", "not_http", "unset"])
def test_no_usable_bridge_reports_the_source_down_and_no_measurements(
    exporter: ModuleType, bridge: FakeBridge, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    if case == "unreachable":
        bridge.close()
    elif case == "http_500":
        bridge.status, bridge.body = 500, ARDUINO_SHUNT
    elif case == "unrelated":
        bridge.body = 'node_load1 0.4\nsovereign_battery_soc_percent 99\nesphome_sensor_value{id="x",name="X"} 1\n'
    elif case == "not_http":
        monkeypatch.setenv("ESP32_BRIDGE_URL", (ROOT / "README.md").as_uri())
    elif case == "unset":
        monkeypatch.delenv("ESP32_BRIDGE_URL")
    assert _scrape(exporter) == {"sovereign_telemetry_simulated": 0.0, "sovereign_telemetry_source_up": 0.0}


def test_bridge_is_scraped_at_most_once_per_cache_interval(exporter: ModuleType, bridge: FakeBridge) -> None:
    bridge.body = ARDUINO_SHUNT
    exporter._bridge_cache = None
    first = exporter.get_telemetry_metrics()
    assert exporter.get_telemetry_metrics() == first
    assert bridge.hits == 1


def test_bridge_failure_is_logged_once_per_streak(
    exporter: ModuleType, bridge: FakeBridge, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    bridge.body = ""
    for _ in range(3):
        _scrape(exporter)
    assert caplog.text.count("Hardware telemetry unavailable") == 1
    bridge.body = ARDUINO_SHUNT
    _scrape(exporter)
    assert caplog.text.count("Hardware telemetry source is up") == 1


def test_non_finite_and_malformed_bridge_samples_are_ignored() -> None:
    readings = telemetry.parse_bridge_metrics(
        "sovereign_hw_battery_soc_percent NaN\n"
        "sovereign_hw_battery_voltage_volts +Inf\n"
        "sovereign_hw_battery_current_amperes abc\n"
        'sovereign_hw_solar_power_watts{instance="x"} 5\n'  # the sketch exports no labels
        'esphome_sensor_value{id="battery_soc",name="Battery SoC",unit="%"} nan\n'
        'esphome_sensor_value{id="solar_power",name="Renamed \\"PV\\"",unit="W"} 12\n'  # matched by id
        "this is not a sample\n"
    )
    assert readings == {"solar_power": 12.0}


# --- end to end: bridge -> exporter HTTP endpoint -> HAL / sentinel ---------------------------


@pytest.fixture
def exporter_url(bridge: FakeBridge, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The packaged exporter in hardware mode, served on a loopback port."""
    from http.server import HTTPServer

    monkeypatch.setattr(telemetry, "SIMULATION", False)
    monkeypatch.setattr(telemetry, "_bridge_cache", None)
    monkeypatch.setattr(telemetry, "BRIDGE_CACHE_SECONDS", 0.0)
    server = HTTPServer(("127.0.0.1", 0), telemetry.MetricsHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/metrics"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("body", [ARDUINO_SHUNT, ESPHOME_SHUNT], ids=["arduino", "esphome"])
def test_hal_reads_measured_values_through_the_exporter_in_hardware_mode(
    bridge: FakeBridge, exporter_url: str, body: str
) -> None:
    bridge.body = body
    power = read_power(exporter_url, simulation=False)
    assert power.is_simulated is False
    assert power.battery_soc == 87.6
    assert power.battery_voltage == 26.41
    assert power.solar_watts is None and power.system_load_watts is None  # not measured, not invented
    assert power.load_shedding_active is False

    thermal = read_thermal(exporter_url, simulation=False)
    assert thermal.is_simulated is False
    assert thermal.coolant_celsius == 38.5
    assert thermal.rack_inlet_celsius is None and thermal.rack_exhaust_celsius is None


def test_hal_refuses_a_stale_or_absent_bridge_in_hardware_mode(bridge: FakeBridge, exporter_url: str) -> None:
    bridge.body = ARDUINO_STALE
    with pytest.raises(TelemetryUnavailable):
        read_power(exporter_url, simulation=False)
    with pytest.raises(TelemetryUnavailable):
        read_thermal(exporter_url, simulation=False)  # source down: nothing is a measurement
    bridge.body = ARDUINO_MPPT
    with pytest.raises(TelemetryUnavailable, match="sovereign_battery_soc_percent"):
        read_power(exporter_url, simulation=False)


def test_sentinel_decides_from_the_bridge_soc(
    bridge: FakeBridge, exporter_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sentinel_copilot, "POWER_EXPORTER_URL", exporter_url)
    bridge.body = "sovereign_hw_battery_soc_percent 10.0\nsovereign_hw_vedirect_last_frame_age_seconds 0.5\n"
    decision = sentinel_copilot.evaluate_mode(sentinel_copilot.get_telemetry(), forecast=800.0)
    assert decision is not None and decision[0] == "ECO_PRESERVATION"


# --- the firmware and the exporter cannot drift apart ------------------------------------------


def test_exporter_mapping_covers_every_series_of_the_arduino_sketch() -> None:
    sketch = (ROOT / "firmware" / "esp32_telemetry_bridge.ino").read_text(encoding="utf-8")
    metrics_fn = sketch[sketch.index("void handleMetrics()") : sketch.index("void logReading(")]
    exported = set(re.findall(r"\bsovereign_hw_[a-z0-9_]+", metrics_fn))
    assert len(exported) >= 9
    assert exported == set(telemetry.ARDUINO_BRIDGE_SERIES)
    # Every measurement gauge has a canonical target; only the two frame counters are not re-exported.
    assert {name for name, target in telemetry.ARDUINO_BRIDGE_SERIES.items() if target is None} == {
        "sovereign_hw_vedirect_frames_valid_total",
        "sovereign_hw_vedirect_checksum_errors_total",
    }


def test_exporter_mapping_covers_every_sensor_of_the_esphome_config() -> None:
    config = (ROOT / "firmware" / "esphome_smdc_bridge.yaml").read_text(encoding="utf-8")
    section = config[config.index("\nsensor:\n") : config.index("\nbinary_sensor:\n")]
    names = {name.lower() for name in re.findall(r'^\s+name: "([^"]+)"', section, re.MULTILINE)}
    assert len(names) >= 11
    assert names == set(telemetry.ESPHOME_BRIDGE_SENSORS)
    mapped_ids = set(re.findall(r"^\s+id: ([a-z_]+)", section, re.MULTILINE)) - {
        "solar_voltage",
        "wifi_rssi",
        "uptime_sensor",
    }
    assert mapped_ids <= set(telemetry.ESPHOME_BRIDGE_SENSOR_IDS)
    assert set(telemetry.ESPHOME_BRIDGE_SENSOR_IDS.values()) <= set(telemetry.ESPHOME_BRIDGE_SENSORS)
    # Both flavours feed the same quantities (the sketch has no yield reading).
    arduino = {target[0] for target in telemetry.ARDUINO_BRIDGE_SERIES.values() if target}
    esphome = {target[0] for target in telemetry.ESPHOME_BRIDGE_SENSORS.values() if target}
    assert esphome - arduino == {"solar_yield_kwh"} and arduino <= esphome


def _bridge_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text[text.index("# --- BEGIN ESP32 BRIDGE READER") : text.index("# --- END ESP32 BRIDGE READER")]


def test_bridge_reader_is_identical_in_both_exporters() -> None:
    assert _bridge_block(PACKAGED) == _bridge_block(STANDALONE)


def test_canonical_names_match_what_the_consumers_read() -> None:
    from sovereign_dc.hal import power, thermal

    canonical = {name for name, _, _, _ in telemetry.CANONICAL_SERIES}
    consumed = {
        power.METRIC_BATTERY_SOC,
        power.METRIC_SOLAR_WATTS,
        power.METRIC_BATTERY_VOLTAGE,
        power.METRIC_SYSTEM_LOAD,
        power.METRIC_LOAD_SHEDDING,
        thermal.METRIC_COOLANT,
        thermal.METRIC_RACK_INLET,
        thermal.METRIC_RACK_EXHAUST,
        sentinel_copilot.SOC_METRIC,
        sentinel_copilot.SOLAR_METRIC,
    }
    assert consumed <= canonical
    shedder = (ROOT / "software" / "telemetry" / "load_shedder.sh").read_text(encoding="utf-8")
    assert set(re.findall(r"\^(sovereign_[a-z_]+) ", shedder)) <= canonical


def test_simulated_output_still_carries_every_series_the_consumers_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry, "SIMULATION", True)
    series = _series(telemetry.get_telemetry_metrics())
    assert series["sovereign_telemetry_simulated"] == 1.0 and series["sovereign_telemetry_source_up"] == 1.0
    for name in (
        "sovereign_battery_soc_percent",
        "sovereign_battery_voltage_volts",
        "sovereign_battery_current_amperes",
        "sovereign_solar_pv_power_watts",
        "sovereign_solar_daily_yield_kwh",
        "sovereign_system_power_draw_watts",
        "sovereign_temp_rack_inlet_celsius",
        "sovereign_temp_rack_exhaust_celsius",
        "sovereign_temp_coolant_celsius",
        "sovereign_load_shedding_active",
    ):
        assert name in series
    assert "sovereign_temp_coolant_intake_celsius" not in series  # bridge-only series


def test_compose_passes_the_bridge_url_to_the_exporter() -> None:
    compose = (ROOT / "software" / "telemetry" / "docker-compose.telemetry.yml").read_text(encoding="utf-8")
    assert "- ESP32_BRIDGE_URL=${ESP32_BRIDGE_URL:-}" in compose
