#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Energy-Aware Datacenter Sentinel Copilot
Optimizes background AI jobs, model fine-tuning, and batch vectorization based on solar yield.
"""

import logging
import math
import os
import time
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SentinelCopilot] %(message)s")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
POWER_EXPORTER_URL = os.getenv("POWER_EXPORTER_URL", "http://power-exporter:9101/metrics")


def get_telemetry() -> dict[str, float]:
    try:
        req = urllib.request.Request(POWER_EXPORTER_URL, headers={"User-Agent": "sentinel-copilot"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            lines = resp.read().decode("utf-8").split("\n")
            metrics = {}
            for line in lines:
                if line and not line.startswith("#"):
                    parts = line.split()
                    if len(parts) == 2:
                        metrics[parts[0]] = float(parts[1])
            return metrics
    except Exception as e:
        logging.warning(f"Could not reach power exporter: {e}")
        return {}


def get_solar_forecast() -> float:
    """Returns a mocked 4-hour forward prediction of solar yield (Watts) based on time of day."""
    # In a real scenario, this would query Open-Meteo or local atmospheric models.
    hour = (time.time() / 3600) % 24
    future_hour = (hour + 4.0) % 24

    # Simple bell curve for daylight between 6am and 8pm (20:00)
    if 6 <= future_hour <= 20:
        daylight = max(0.0, math.sin(math.pi * (future_hour - 6) / 14))
        return daylight * 1500.0  # Max 1500W predicted
    return 0.0


def set_mode(mode: str) -> None:
    """Writes the current mode to a shared state file for other agents to consume."""
    try:
        with open("/tmp/sovereign_mode", "w") as f:
            f.write(mode)
    except Exception as e:
        logging.warning(f"Failed to write mode state: {e}")


def run_copilot() -> None:
    logging.info("Starting Energy-Aware Datacenter Sentinel Copilot...")
    current_mode = "NORMAL"

    while True:
        metrics = get_telemetry()
        soc = metrics.get("sovereign_battery_soc_percent", 85.0)
        solar = metrics.get("sovereign_solar_pv_power_watts", 1000.0)

        forecast = get_solar_forecast()

        # Predictive load shedding:
        # If battery is currently somewhat okay (e.g. < 40%) but forecast is very low (e.g. night time),
        # preemptively shed load to preserve battery through the night.
        is_critical = soc < 25.0
        is_predicted_critical = soc < 40.0 and forecast < 100.0

        if is_critical or is_predicted_critical:
            if current_mode != "ECO_PRESERVATION":
                current_mode = "ECO_PRESERVATION"
                set_mode(current_mode)
                reason = (
                    f"Battery SoC at {soc:.1f}%"
                    if is_critical
                    else f"Predictive (SoC {soc:.1f}%, Forecast {forecast:.0f}W)"
                )
                logging.warning(
                    f"⚡ Sentinel Trigger: {reason}. Throttling non-essential AI batch jobs to conserve power!"
                )
        elif solar > 1000.0 and soc > 75.0:
            if current_mode != "SOLAR_SURPLUS_COMPUTE":
                current_mode = "SOLAR_SURPLUS_COMPUTE"
                set_mode(current_mode)
                logging.info(
                    f"☀️ Solar Surplus ({solar:.0f}W, {soc:.1f}% SoC): Unlocking full GPU compute capacity for model training & batch vectorization."
                )
        else:
            if current_mode != "NORMAL":
                current_mode = "NORMAL"
                set_mode(current_mode)
                logging.info(f"Nominal operating conditions ({solar:.0f}W, {soc:.1f}% SoC, Forecast {forecast:.0f}W).")

        time.sleep(30)


if __name__ == "__main__":
    run_copilot()
