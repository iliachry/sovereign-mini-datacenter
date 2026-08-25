"""Industrial IoT Edge Gateway for Sovereign Mini Datacenter (SMDC).

Demonstrates low-overhead sensor telemetry polling, L0 critical priority execution,
and integration with the SMDC SDK.
"""

from __future__ import annotations

import logging
import random
import time

from sovereign_dc.enterprise.sdk import AppLifecycleHandler, SMDCClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("iot-edge-gateway")


def main() -> None:
    logger.info("Initializing Industrial IoT Edge Gateway...")
    client = SMDCClient()
    lifecycle = AppLifecycleHandler("iot-edge-gateway", client=client)

    logger.info("Connected to SMDC node telemetry bus.")

    iteration = 0
    while lifecycle.is_running:
        iteration += 1

        # Read local DC environmental telemetry
        dc_telemetry = client.get_telemetry()
        soc = dc_telemetry.get("battery_soc", 100.0)
        solar = dc_telemetry.get("solar_watts", 0.0)

        # Ingest simulated industrial sensors (temperature, vibration, pressure)
        sensor_data = {
            "vibration_rms": round(random.uniform(0.1, 0.45), 3),
            "bearing_temp_c": round(random.uniform(42.0, 58.5), 1),
            "line_pressure_bar": round(random.uniform(5.8, 6.2), 2),
            "iteration": iteration,
            "dc_battery_soc": soc,
            "dc_solar_w": solar,
        }

        # Publish to SMDC enterprise event stream
        client.emit_telemetry("iot-edge-gateway", sensor_data)
        logger.info(
            "Iter #%d | Temp: %.1f°C | Vibration: %.3f RMS | Solar: %.1fW | Battery: %.1f%%",
            iteration,
            sensor_data["bearing_temp_c"],
            sensor_data["vibration_rms"],
            solar,
            soc,
        )

        time.sleep(5.0)

    logger.info("Industrial IoT Edge Gateway shut down cleanly.")


if __name__ == "__main__":
    main()
