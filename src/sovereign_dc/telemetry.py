"""
Sovereign Mini Datacenter - Power, Solar & Thermal Prometheus Exporter
"""

import logging
import math
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PORT = int(os.getenv("EXPORTER_PORT", "9101"))
SIMULATION = os.getenv("SIMULATE_POWER_DATA", "true").lower() in ("true", "1", "yes")

start_time = time.time()
fault_overrides: dict[str, float] = {}


def get_telemetry_metrics():
    """Collects hardware telemetry or generates realistic physics-based simulation data."""
    t = time.time() - start_time

    if SIMULATION:
        hour = (time.time() / 3600) % 24
        daylight = max(0.0, math.sin(math.pi * (hour - 6) / 14)) if 6 <= hour <= 20 else 0.0

        solar_watts = daylight * 1640.0 * (0.85 + 0.15 * math.sin(t / 10.0))
        base_load_watts = 280.0 + 80.0 * math.sin(t / 45.0)
        net_power = solar_watts - base_load_watts

        soc_percent = min(100.0, max(15.0, 78.0 + 15.0 * math.sin(t / 600.0)))
        battery_voltage = 48.0 + (soc_percent / 100.0) * 5.6
        battery_current = net_power / max(1.0, battery_voltage)

        rack_inlet_celsius = 21.5 + 2.0 * math.sin(t / 120.0)
        coolant_temp_celsius = 28.0 + (base_load_watts / 400.0) * 12.0
        rack_exhaust_celsius = rack_inlet_celsius + (base_load_watts / 350.0) * 8.5

        daily_yield_kwh = max(1.2, 8.4 * daylight)
        load_shedding = 1.0 if soc_percent < 20.0 else 0.0
    else:
        solar_watts = 0.0
        base_load_watts = 300.0
        soc_percent = 85.0
        battery_voltage = 53.2
        battery_current = -5.6
        rack_inlet_celsius = 22.0
        rack_exhaust_celsius = 29.0
        coolant_temp_celsius = 32.0
        daily_yield_kwh = 4.5
        load_shedding = 0.0

    # Apply fault overrides
    if "soc" in fault_overrides:
        soc_percent = float(fault_overrides["soc"])
    if "temp" in fault_overrides:
        coolant_temp_celsius = float(fault_overrides["temp"])
        rack_inlet_celsius = coolant_temp_celsius - 8.0
        rack_exhaust_celsius = coolant_temp_celsius + 5.0

    lines = [
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
    def do_GET(self):
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

    def do_POST(self):
        if self.path == "/fault":
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)
            try:
                import json

                data = json.loads(post_data.decode("utf-8"))
                fault_overrides.update(data)
                logging.warning(f"Applied fault overrides: {data}")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "overrides": fault_overrides}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"Error parsing fault JSON: {e}".encode())
        elif self.path == "/fault/clear":
            fault_overrides.clear()
            logging.info("Cleared fault overrides")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status": "cleared"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def run(port=PORT, simulation=SIMULATION):
    global PORT, SIMULATION
    PORT = port
    SIMULATION = simulation
    server = HTTPServer(("0.0.0.0", PORT), MetricsHandler)
    logging.info(f"Sovereign Power & Thermal Exporter listening on :http://0.0.0.0:{PORT}/metrics (Sim={SIMULATION})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down exporter.")
        server.server_close()


if __name__ == "__main__":
    run()
