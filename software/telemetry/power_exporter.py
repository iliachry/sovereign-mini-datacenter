#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Power, Solar & Thermal Prometheus Exporter
Standard library implementation (no external pip dependencies required).
"""

import logging
import math
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger("PowerExporter")

PORT = int(os.getenv("EXPORTER_PORT", "9101"))
SIMULATION = os.getenv("SIMULATE_POWER_DATA", "true").lower() in ("true", "1", "yes")
# Offset (hours, east positive) added to UTC to approximate local solar time in the
# simulation. Default 0 = site on the Greenwich meridian; set to site_longitude_deg / 15.
SOLAR_UTC_OFFSET_HOURS = float(os.getenv("SOLAR_UTC_OFFSET_HOURS", "0") or 0.0)

start_time = time.time()
_warned_no_hardware = False


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

    No hardware reader (VE.Direct / RS485) is implemented in this exporter. With
    SIMULATE_POWER_DATA=false it therefore reports ``sovereign_telemetry_source_up 0``
    and omits every measurement series instead of publishing constants that would look
    like live readings.
    """
    global _warned_no_hardware
    t = time.time() - start_time

    if not SIMULATION:
        if not _warned_no_hardware:
            logger.error(
                "Hardware telemetry requested (SIMULATE_POWER_DATA=false) but no hardware reader is "
                "implemented; exporting sovereign_telemetry_source_up=0 and no measurements."
            )
            _warned_no_hardware = True
        return "\n".join(_source_status_lines(simulated=False, source_up=False)) + "\n"

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
    load_shedding = 1.0 if soc_percent < 20.0 else 0.0

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
