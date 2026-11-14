#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Power, Solar & Thermal Prometheus Exporter
Standard library implementation (no external pip dependencies required).

The one component that owns the canonical ``sovereign_*`` power and thermal series. It has
two sources: a physics-shaped simulation (SIMULATE_POWER_DATA=true, the default) and, with
SIMULATE_POWER_DATA=false, the ESP32 telemetry bridge named by ESP32_BRIDGE_URL (see the
"ESP32 BRIDGE READER" block below for the exact mapping). ``src/sovereign_dc/telemetry.py``
(``smdc telemetry``) is the packaged copy of the same exporter.
"""

import logging
import math
import os
import re
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger("PowerExporter")

PORT = int(os.getenv("EXPORTER_PORT", "9101"))
SIMULATION = os.getenv("SIMULATE_POWER_DATA", "true").lower() in ("true", "1", "yes")
# Offset (hours, east positive) added to UTC to approximate local solar time in the
# simulation. Default 0 = site on the Greenwich meridian; set to site_longitude_deg / 15.
SOLAR_UTC_OFFSET_HOURS = float(os.getenv("SOLAR_UTC_OFFSET_HOURS", "0") or 0.0)

start_time = time.time()

# --- BEGIN ESP32 BRIDGE READER -------------------------------------------------------------
# This block is kept byte-identical in src/sovereign_dc/telemetry.py and
# software/telemetry/power_exporter.py (tests/test_regress_round3_bridge.py compares them).
#
# Hardware path of the exporter: with simulation off and ESP32_BRIDGE_URL set, every scrape
# reads the ESP32 telemetry bridge's own /metrics (firmware/) and re-exports its readings
# under the canonical ``sovereign_*`` names that the HAL, the sentinel, load_shedder.sh and
# the Grafana dashboards read. Both firmware flavours are understood:
#
#   Arduino sketch  plain series               sovereign_hw_battery_soc_percent 87.6
#   ESPHome         labelled sensor rows       esphome_sensor_value{id="battery_soc",name="Battery SoC",unit="%"} 87.6
#                   (matched by the ``name`` label, then by ``id``; a sensor that reports
#                   ``esphome_sensor_failed ... 1`` has no value row and is simply absent)
#
#   Bridge reading (Arduino series / ESPHome sensor name)                unit   canonical series
#   sovereign_hw_battery_soc_percent / "Battery SoC"                     %      sovereign_battery_soc_percent
#   sovereign_hw_battery_voltage_volts / "Battery Voltage"               V      sovereign_battery_voltage_volts
#   sovereign_hw_battery_current_amperes / "Battery Current"             A      sovereign_battery_current_amperes
#   sovereign_hw_solar_power_watts / "Solar Harvest Power"               W      sovereign_solar_pv_power_watts
#   (ESPHome only) "Solar Yield Today"                                   Wh     sovereign_solar_daily_yield_kwh (/1000)
#   sovereign_hw_coolant_intake_temp_celsius / "Coolant Loop Intake Temp"  C    sovereign_temp_coolant_intake_celsius
#   sovereign_hw_coolant_exhaust_temp_celsius / "Radiator Exhaust Temp"    C    sovereign_temp_coolant_exhaust_celsius
#   the hotter of the two coolant-loop probes that answered              C      sovereign_temp_coolant_celsius
#   sovereign_hw_vedirect_last_frame_age_seconds /
#       "VE.Direct Last Valid Frame Age"                                 s      sovereign_telemetry_bridge_frame_age_seconds
#   derived: 1 if SoC < SHEDDING_L3_SOC (default 20 %), else 0
#       (only when SoC was read)                                                sovereign_load_shedding_active
#
# Both DS18B20 probes sit on the coolant loop (firmware/README.md), so neither is an air
# temperature: sovereign_temp_rack_inlet_celsius and sovereign_temp_rack_exhaust_celsius are
# NOT exported in hardware mode, and neither is sovereign_system_power_draw_watts (the
# bridge has no load measurement). A canonical series the bridge did not supply is omitted,
# never filled in. "Solar Array Voltage", the VE.Direct frame counters and the ESPHome Wi-Fi
# and uptime sensors have no canonical series and are not re-exported.
#
# sovereign_telemetry_source_up is 0 when the bridge is unreachable, returns none of the
# readings above, or reports a last-valid-frame age above ESP32_BRIDGE_MAX_FRAME_AGE_SECONDS
# (default 10 s, the firmware's own staleness limit). In the stale case the VE.Direct
# readings are withheld; the coolant temperatures, which the firmware expires on their own
# clock, are still exported.

# sovereign_load_shedding_active is 1 at shedding level 3 and above, i.e. while SoC is below
# the level-3 threshold of the node's one SoC policy (SovereignConfig.shedding_l3_soc, read
# here from the same SHEDDING_L3_SOC environment variable with the same default).
SHEDDING_L3_SOC_ENV = "SHEDDING_L3_SOC"
DEFAULT_LOAD_SHEDDING_SOC_PERCENT = 20.0


def load_shedding_soc_percent() -> float:
    """SoC (percent) below which ``sovereign_load_shedding_active`` is 1: ``SHEDDING_L3_SOC``."""
    raw = os.getenv(SHEDDING_L3_SOC_ENV, "").strip()
    if not raw:
        return DEFAULT_LOAD_SHEDDING_SOC_PERCENT
    try:
        percent = float(raw)
    except ValueError:
        percent = math.nan
    if not math.isfinite(percent):
        logger.warning(
            "Ignoring %s=%r (need a number); using %g", SHEDDING_L3_SOC_ENV, raw, DEFAULT_LOAD_SHEDDING_SOC_PERCENT
        )
        return DEFAULT_LOAD_SHEDDING_SOC_PERCENT
    return percent


LOAD_SHEDDING_SOC_PERCENT = load_shedding_soc_percent()
BRIDGE_CACHE_SECONDS = 1.0
BRIDGE_MAX_BODY_BYTES = 262_144
DEFAULT_BRIDGE_TIMEOUT_SECONDS = 2.0
DEFAULT_BRIDGE_MAX_FRAME_AGE_SECONDS = 10.0

# Quantities read from the bridge that come from the VE.Direct port (withheld when stale).
VEDIRECT_QUANTITIES = ("battery_soc", "battery_voltage", "battery_current", "solar_power", "solar_yield_kwh")

# Arduino flavour: series name -> (quantity, scale), or None for a series that is read
# deliberately but not re-exported. tests/ parses the sketch and fails on an unlisted name.
ARDUINO_BRIDGE_SERIES: dict[str, tuple[str, float] | None] = {
    "sovereign_hw_battery_soc_percent": ("battery_soc", 1.0),
    "sovereign_hw_battery_voltage_volts": ("battery_voltage", 1.0),
    "sovereign_hw_battery_current_amperes": ("battery_current", 1.0),
    "sovereign_hw_solar_power_watts": ("solar_power", 1.0),
    "sovereign_hw_coolant_intake_temp_celsius": ("coolant_intake", 1.0),
    "sovereign_hw_coolant_exhaust_temp_celsius": ("coolant_exhaust", 1.0),
    "sovereign_hw_vedirect_last_frame_age_seconds": ("frame_age", 1.0),
    "sovereign_hw_vedirect_frames_valid_total": None,
    "sovereign_hw_vedirect_checksum_errors_total": None,
}

# ESPHome flavour: sensor ``name`` (lower-cased) -> (quantity, scale), or None (not re-exported).
ESPHOME_BRIDGE_SENSORS: dict[str, tuple[str, float] | None] = {
    "battery soc": ("battery_soc", 1.0),
    "battery voltage": ("battery_voltage", 1.0),
    "battery current": ("battery_current", 1.0),
    "solar harvest power": ("solar_power", 1.0),
    "solar yield today": ("solar_yield_kwh", 0.001),  # Wh -> kWh
    "coolant loop intake temp": ("coolant_intake", 1.0),
    "radiator exhaust temp": ("coolant_exhaust", 1.0),
    "ve.direct last valid frame age": ("frame_age", 1.0),
    "solar array voltage": None,
    "wifi signal": None,
    "bridge uptime": None,
}

# ESPHome ``id`` labels accepted when the name label is missing or was relabelled: the
# object id ESPHome derives from the name, and the id used in esphome_smdc_bridge.yaml.
ESPHOME_BRIDGE_SENSOR_IDS: dict[str, str] = {
    "battery_soc": "battery soc",
    "battery_voltage": "battery voltage",
    "battery_current": "battery current",
    "solar_harvest_power": "solar harvest power",
    "solar_power": "solar harvest power",
    "solar_yield_today": "solar yield today",
    "coolant_loop_intake_temp": "coolant loop intake temp",
    "coolant_intake": "coolant loop intake temp",
    "radiator_exhaust_temp": "radiator exhaust temp",
    "coolant_exhaust": "radiator exhaust temp",
    "ve_direct_last_valid_frame_age": "ve.direct last valid frame age",
    "ve_frame_age": "ve.direct last valid frame age",
}

# Canonical series, in output order: (series name, HELP text, value key, decimals).
CANONICAL_SERIES: tuple[tuple[str, str, str, int], ...] = (
    ("sovereign_battery_soc_percent", "Battery Bank State of Charge (0-100%)", "battery_soc", 2),
    ("sovereign_battery_voltage_volts", "Battery Bank Terminal Voltage", "battery_voltage", 2),
    (
        "sovereign_battery_current_amperes",
        "Battery Pack Current (Amps, + charging, - discharging)",
        "battery_current",
        2,
    ),
    ("sovereign_solar_pv_power_watts", "Instantaneous Solar PV Input Power (Watts)", "solar_power", 2),
    ("sovereign_solar_daily_yield_kwh", "Cumulative Daily Solar Yield (kWh)", "solar_yield_kwh", 3),
    ("sovereign_system_power_draw_watts", "Total Datacenter Electrical Power Draw (Watts)", "system_load", 2),
    ("sovereign_temp_rack_inlet_celsius", "Enclosure Front Intake Air Temperature (C)", "rack_inlet", 2),
    (
        "sovereign_temp_rack_exhaust_celsius",
        "Enclosure Rear Radiator Exhaust Air Temperature (C)",
        "rack_exhaust",
        2,
    ),
    ("sovereign_temp_coolant_celsius", "Liquid Cooling Loop Coolant Temperature (C)", "coolant", 2),
    (
        "sovereign_temp_coolant_intake_celsius",
        "Coolant Loop Intake Probe Temperature (C, ESP32 bridge)",
        "coolant_intake",
        2,
    ),
    (
        "sovereign_temp_coolant_exhaust_celsius",
        "Coolant Loop Radiator Exhaust Probe Temperature (C, ESP32 bridge)",
        "coolant_exhaust",
        2,
    ),
    (
        "sovereign_load_shedding_active",
        "1 while battery SoC is below the level-3 shedding threshold (SHEDDING_L3_SOC, default 20 %), else 0",
        "load_shedding",
        0,
    ),
    (
        "sovereign_telemetry_bridge_frame_age_seconds",
        "Seconds since the ESP32 bridge's last checksum-valid VE.Direct frame",
        "frame_age",
        1,
    ),
)

_SAMPLE_RE = re.compile(r"^([A-Za-z_:][A-Za-z0-9_:]*)(?:\{(.*)\})?\s+(\S+)(?:\s+-?\d+)?\s*$")
_LABEL_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')

_bridge_cache: tuple[float, str, dict[str, float], str | None] | None = None
_bridge_failure_logged: str | None = None


def render_series(values: dict[str, float]) -> list[str]:
    """Exposition lines for the canonical series that have a value (the others are omitted)."""
    lines: list[str] = []
    for name, help_text, key, decimals in CANONICAL_SERIES:
        value = values.get(key)
        if value is None:
            continue
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge", f"{name} {value:.{decimals}f}", ""]
    return lines


def _env_seconds(name: str, default: float) -> float:
    """A positive, finite number of seconds from the environment (else ``default``)."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        seconds = float(raw)
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds <= 0:
        logger.warning("Ignoring %s=%r (need a positive number); using %g", name, raw, default)
        return default
    return seconds


def bridge_url() -> str:
    """ESP32_BRIDGE_URL (e.g. ``http://192.168.1.50/metrics``); empty when not configured."""
    return os.getenv("ESP32_BRIDGE_URL", "").strip()


def parse_bridge_metrics(text: str) -> dict[str, float]:
    """Extracts the bridge's readings from its ``/metrics`` text (either firmware flavour).

    Returns ``{quantity: value}`` in canonical units. Unknown series, sensors with no
    canonical counterpart and non-finite samples are ignored.
    """
    readings: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE_RE.match(line)
        if match is None:
            continue
        series, raw_labels, raw_value = match.groups()
        try:
            value = float(raw_value)
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        target: tuple[str, float] | None = None
        if series == "esphome_sensor_value":
            labels = {key: val.replace('\\"', '"') for key, val in _LABEL_RE.findall(raw_labels or "")}
            name = labels.get("name", "").strip().lower()
            if name not in ESPHOME_BRIDGE_SENSORS:
                name = ESPHOME_BRIDGE_SENSOR_IDS.get(labels.get("id", "").strip().lower(), "")
            target = ESPHOME_BRIDGE_SENSORS.get(name)
        elif raw_labels is None:
            target = ARDUINO_BRIDGE_SERIES.get(series)
        if target is not None:
            readings[target[0]] = value * target[1]
    return readings


def bridge_values(readings: dict[str, float], max_frame_age: float) -> tuple[dict[str, float], str | None]:
    """Maps bridge readings to canonical values. Returns ``(values, why the source is down or None)``."""
    values = dict(readings)
    reason: str | None = None
    frame_age = values.get("frame_age")
    if frame_age is not None and frame_age > max_frame_age:
        reason = f"last valid VE.Direct frame is {frame_age:.1f} s old (limit {max_frame_age:g} s)"
        for key in VEDIRECT_QUANTITIES:
            values.pop(key, None)
    probes = [values[key] for key in ("coolant_intake", "coolant_exhaust") if key in values]
    if probes:
        values["coolant"] = max(probes)
    if "battery_soc" in values:
        values["load_shedding"] = 1.0 if values["battery_soc"] < LOAD_SHEDDING_SOC_PERCENT else 0.0
    if reason is None and not any(key != "frame_age" for key in values):
        reason = "the bridge returned none of the expected readings"
    return values, reason


def read_bridge(url: str) -> tuple[dict[str, float], str | None]:
    """Scrapes the bridge (cached for ``BRIDGE_CACHE_SECONDS``). See ``bridge_values``."""
    global _bridge_cache
    now = time.monotonic()
    cached = _bridge_cache
    if cached is not None and cached[1] == url and 0.0 <= now - cached[0] < BRIDGE_CACHE_SECONDS:
        return dict(cached[2]), cached[3]
    values: dict[str, float] = {}
    reason: str | None
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        reason = "ESP32_BRIDGE_URL must be an http:// or https:// URL"
    else:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "smdc-power-exporter"})
            timeout = _env_seconds("ESP32_BRIDGE_TIMEOUT_SECONDS", DEFAULT_BRIDGE_TIMEOUT_SECONDS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - scheme checked above
                text = resp.read(BRIDGE_MAX_BODY_BYTES).decode("utf-8", errors="replace")
        except Exception as e:
            reason = f"bridge unreachable: {e}"
        else:
            max_age = _env_seconds("ESP32_BRIDGE_MAX_FRAME_AGE_SECONDS", DEFAULT_BRIDGE_MAX_FRAME_AGE_SECONDS)
            values, reason = bridge_values(parse_bridge_metrics(text), max_age)
    _bridge_cache = (now, url, dict(values), reason)
    return values, reason


def hardware_metrics_lines() -> tuple[list[str], bool]:
    """Measurement lines for hardware mode and whether the source is up (never simulated)."""
    global _bridge_failure_logged
    url = bridge_url()
    if not url:
        reason: str | None = (
            "ESP32_BRIDGE_URL is not set (the ESP32 bridge is the only hardware source this exporter reads)"
        )
        values: dict[str, float] = {}
    else:
        values, reason = read_bridge(url)
    if reason != _bridge_failure_logged:
        if reason is None:
            logger.info("Hardware telemetry source is up (%s).", url)
        else:
            logger.error(
                "Hardware telemetry unavailable: %s; exporting sovereign_telemetry_source_up=0.",
                reason,
            )
        _bridge_failure_logged = reason
    return render_series(values), reason is None


# --- END ESP32 BRIDGE READER ---------------------------------------------------------------


def _source_status_lines(simulated: bool, source_up: bool) -> list[str]:
    return [
        "# HELP sovereign_telemetry_simulated 1 if the values below are simulated, 0 if measured",
        "# TYPE sovereign_telemetry_simulated gauge",
        f"sovereign_telemetry_simulated {1 if simulated else 0}",
        "",
        "# HELP sovereign_telemetry_source_up 1 if a telemetry source is providing data, 0 otherwise",
        "# TYPE sovereign_telemetry_source_up gauge",
        f"sovereign_telemetry_source_up {1 if source_up else 0}",
        "",
    ]


def get_telemetry_metrics() -> str:
    """Returns Prometheus exposition text: simulated physics data, or the ESP32 bridge's readings.

    With SIMULATE_POWER_DATA=false the measurements come from the ESP32 telemetry bridge
    named by ESP32_BRIDGE_URL (``hardware_metrics_lines``); VE.Direct and RS485 are not read
    directly. Without a reachable, fresh bridge the exporter reports
    ``sovereign_telemetry_source_up 0`` instead of publishing constants that would look like
    live readings, and a series the bridge did not supply is omitted.
    """
    if not SIMULATION:
        measured, source_up = hardware_metrics_lines()
        return "\n".join(_source_status_lines(simulated=False, source_up=source_up) + measured) + "\n"

    t = time.time() - start_time
    hour = (time.time() / 3600 + SOLAR_UTC_OFFSET_HOURS) % 24
    daylight = max(0.0, math.sin(math.pi * (hour - 6) / 14)) if 6 <= hour <= 20 else 0.0

    solar_watts = daylight * 3280.0 * (0.85 + 0.15 * math.sin(t / 10.0))
    base_load_watts = 280.0 + 80.0 * math.sin(t / 45.0)
    net_power = solar_watts - base_load_watts

    soc_percent = min(100.0, max(15.0, 78.0 + 15.0 * math.sin(t / 600.0)))
    battery_voltage = 48.0 + (soc_percent / 100.0) * 5.6
    battery_current = net_power / max(1.0, battery_voltage)

    rack_inlet_celsius = 21.5 + 2.0 * math.sin(t / 120.0)
    coolant_temp_celsius = 28.0 + (base_load_watts / 400.0) * 12.0
    rack_exhaust_celsius = rack_inlet_celsius + (base_load_watts / 350.0) * 8.5

    daily_yield_kwh = max(1.2, 16.8 * daylight)

    values = {
        "battery_soc": soc_percent,
        "battery_voltage": battery_voltage,
        "battery_current": battery_current,
        "solar_power": solar_watts,
        "solar_yield_kwh": daily_yield_kwh,
        "system_load": base_load_watts,
        "rack_inlet": rack_inlet_celsius,
        "rack_exhaust": rack_exhaust_celsius,
        "coolant": coolant_temp_celsius,
        "load_shedding": 1.0 if soc_percent < LOAD_SHEDDING_SOC_PERCENT else 0.0,
    }
    return "\n".join(_source_status_lines(simulated=True, source_up=True) + render_series(values)) + "\n"


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/metrics" or self.path == "/":
            payload = get_telemetry_metrics().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.path == "/health" or self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    server = HTTPServer(("0.0.0.0", PORT), MetricsHandler)
    logger.info("Sovereign Power & Thermal Exporter listening on http://0.0.0.0:%s/metrics (Sim=%s)", PORT, SIMULATION)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down exporter.")
        server.server_close()


if __name__ == "__main__":
    run()
