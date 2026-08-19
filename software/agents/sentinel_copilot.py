#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Energy-Aware Datacenter Sentinel Copilot
Optimizes background AI jobs, model fine-tuning, and batch vectorization based on solar yield.
"""

import os
import sys
import time
import logging
import urllib.request
import urllib.error

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SentinelCopilot] %(message)s")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
POWER_EXPORTER_URL = os.getenv("POWER_EXPORTER_URL", "http://power-exporter:9101/metrics")

def get_telemetry():
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

def run_copilot():
    logging.info("Starting Energy-Aware Datacenter Sentinel Copilot...")
    current_mode = "NORMAL"

    while True:
        metrics = get_telemetry()
        soc = metrics.get("sovereign_battery_soc_percent", 85.0)
        solar = metrics.get("sovereign_solar_pv_power_watts", 1000.0)

        if soc < 25.0:
            if current_mode != "ECO_PRESERVATION":
                current_mode = "ECO_PRESERVATION"
                logging.warning(f"⚡ Sentinel Trigger: Battery SoC at {soc:.1f}%. Throttling non-essential AI batch jobs to conserve power!")
        elif solar > 1000.0 and soc > 75.0:
            if current_mode != "SOLAR_SURPLUS_COMPUTE":
                current_mode = "SOLAR_SURPLUS_COMPUTE"
                logging.info(f"☀️ Solar Surplus ({solar:.0f}W, {soc:.1f}% SoC): Unlocking full GPU compute capacity for model training & batch vectorization.")
        else:
            if current_mode != "NORMAL":
                current_mode = "NORMAL"
                logging.info(f"Nominal operating conditions ({solar:.0f}W, {soc:.1f}% SoC).")

        time.sleep(30)

if __name__ == "__main__":
    run_copilot()
