"""
Sovereign Mini Datacenter - Power, Solar & Thermal Prometheus Exporter
"""

import json
import logging
import math
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger("Telemetry.Exporter")

_TRUTHY = ("true", "1", "yes")
_FAULT_KEYS = ("soc", "temp")


def _env_port(default: int = 9101) -> int:
    """Parses EXPORTER_PORT, falling back to the default on a malformed value."""
    raw = os.getenv("EXPORTER_PORT", str(default))
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring malformed EXPORTER_PORT=%r; using %d", raw, default)
        return default


def fault_injection_enabled() -> bool:
    """Fault injection is a test facility and stays off unless explicitly enabled."""
    return os.getenv("ENABLE_FAULT_INJECTION", "false").lower() in _TRUTHY


PORT = _env_port()
SIMULATION = os.getenv("SIMULATE_POWER_DATA", "true").lower() in _TRUTHY

start_time = time.time()
fault_overrides: dict[str, float] = {}
_warned_no_hardware = False


def parse_fault_overrides(data: object) -> dict[str, float]:
    """Validates a fault-injection payload: only finite numeric ``soc`` / ``temp`` are accepted."""
    if not isinstance(data, dict):
        raise ValueError("fault payload must be a JSON object")
    parsed: dict[str, float] = {}
    for key, value in data.items():
        if key not in _FAULT_KEYS:
            raise ValueError(f"unsupported fault key: {key!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"fault value for {key!r} must be a finite number")
        parsed[key] = float(value)
    return parsed


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
    """Returns Prometheus exposition text: simulated physics data, or no measurements at all.

    No hardware reader (VE.Direct / RS485) is implemented in this exporter. With simulation
    disabled it therefore reports ``sovereign_telemetry_source_up 0`` and omits every
    measurement series rather than publishing constants that look like live readings.
    """
    global _warned_no_hardware
    t = time.time() - start_time

    if SIMULATION:
        hour = (time.time() / 3600) % 24
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
        load_shedding = 1.0 if soc_percent < 20.0 else 0.0
    else:
        if not _warned_no_hardware:
            logger.error(
                "Hardware telemetry requested but no hardware reader is implemented; "
                "exporting sovereign_telemetry_source_up=0 and no measurements."
            )
            _warned_no_hardware = True
        return "\n".join(_source_status_lines(simulated=False, source_up=False)) + "\n"

    # Apply fault overrides (simulation only; see fault_injection_enabled)
    if "soc" in fault_overrides:
        soc_percent = float(fault_overrides["soc"])
        load_shedding = 1.0 if soc_percent < 20.0 else 0.0
    if "temp" in fault_overrides:
        coolant_temp_celsius = float(fault_overrides["temp"])
        rack_inlet_celsius = coolant_temp_celsius - 8.0
        rack_exhaust_celsius = coolant_temp_celsius + 5.0

    lines = _source_status_lines(simulated=True, source_up=True) + [
        "# HELP sovereign_battery_soc_percent Battery Bank State of Charge (0-100%)",
        "# TYPE sovereign_battery_soc_percent gauge",
        f"sovereign_battery_soc_percent {soc_percent:.2f}",
        "",
        "# HELP sovereign_battery_voltage_volts Battery Bank Terminal Voltage",
        "# TYPE sovereign_battery_voltage_volts gauge",
        f"sovereign_battery_voltage_volts {battery_voltage:.2f}",
        "",
        "# HELP sovereign_battery_current_amperes Battery Pack Current (Amps, + charging, - discharging)",
        "# TYPE sovereign_battery_current_amperes gauge",
        f"sovereign_battery_current_amperes {battery_current:.2f}",
        "",
        "# HELP sovereign_solar_pv_power_watts Instantaneous Solar PV Input Power (Watts)",
        "# TYPE sovereign_solar_pv_power_watts gauge",
        f"sovereign_solar_pv_power_watts {solar_watts:.2f}",
        "",
        "# HELP sovereign_solar_daily_yield_kwh Cumulative Daily Solar Yield (kWh)",
        "# TYPE sovereign_solar_daily_yield_kwh gauge",
        f"sovereign_solar_daily_yield_kwh {daily_yield_kwh:.3f}",
        "",
        "# HELP sovereign_system_power_draw_watts Total Datacenter Electrical Power Draw (Watts)",
        "# TYPE sovereign_system_power_draw_watts gauge",
        f"sovereign_system_power_draw_watts {base_load_watts:.2f}",
        "",
        "# HELP sovereign_temp_rack_inlet_celsius Enclosure Front Intake Air Temperature (C)",
        "# TYPE sovereign_temp_rack_inlet_celsius gauge",
        f"sovereign_temp_rack_inlet_celsius {rack_inlet_celsius:.2f}",
        "",
        "# HELP sovereign_temp_rack_exhaust_celsius Enclosure Rear Radiator Exhaust Air Temperature (C)",
        "# TYPE sovereign_temp_rack_exhaust_celsius gauge",
        f"sovereign_temp_rack_exhaust_celsius {rack_exhaust_celsius:.2f}",
        "",
        "# HELP sovereign_temp_coolant_celsius Liquid Cooling Loop Coolant Temperature (C)",
        "# TYPE sovereign_temp_coolant_celsius gauge",
        f"sovereign_temp_coolant_celsius {coolant_temp_celsius:.2f}",
        "",
        "# HELP sovereign_load_shedding_active 1 if load shedding is active, 0 otherwise",
        "# TYPE sovereign_load_shedding_active gauge",
        f"sovereign_load_shedding_active {load_shedding:.0f}",
        "",
    ]
    return "\n".join(lines) + "\n"


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

    def _send_json(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path not in ("/fault", "/fault/clear"):
            self.send_response(404)
            self.end_headers()
            return
        if not fault_injection_enabled():
            self._send_json(403, {"status": "error", "error": "fault injection disabled (ENABLE_FAULT_INJECTION)"})
            return
        if self.path == "/fault/clear":
            fault_overrides.clear()
            logger.info("Cleared fault overrides")
            self._send_json(200, {"status": "cleared"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length < 0 or content_length > 4096:
                raise ValueError("invalid Content-Length")
            overrides = parse_fault_overrides(json.loads(self.rfile.read(content_length).decode("utf-8")))
        except ValueError as e:
            self._send_json(400, {"status": "error", "error": str(e)})
            return
        fault_overrides.update(overrides)
        logger.warning("Applied fault overrides: %s", overrides)
        self._send_json(200, {"status": "ok", "overrides": dict(fault_overrides)})

    def log_message(self, format: str, *args: object) -> None:
        """Suppress per-request access logs."""


def run(port: int | None = None, simulation: bool | None = None) -> None:
    global PORT, SIMULATION
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    PORT = PORT if port is None else port
    SIMULATION = SIMULATION if simulation is None else simulation
    # Prometheus scrapes this exporter from another container, so it binds every interface.
    server = HTTPServer(("0.0.0.0", PORT), MetricsHandler)  # noqa: S104
    logger.info("Sovereign Power & Thermal Exporter listening on http://0.0.0.0:%d/metrics (Sim=%s)", PORT, SIMULATION)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down exporter.")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
