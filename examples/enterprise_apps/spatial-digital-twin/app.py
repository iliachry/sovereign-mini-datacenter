"""Spatial 3D Digital Twin Engine for Sovereign Mini Datacenter (SMDC).

Demonstrates real-time telemetry streaming into interactive 3D spatial twins
with WebGL assets and dynamic power management.
"""

from __future__ import annotations

import logging
import time

from sovereign_dc.enterprise.sdk import AppLifecycleHandler, SMDCClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("spatial-digital-twin")


def main() -> None:
    logger.info("Starting Spatial 3D Digital Twin Engine...")
    client = SMDCClient()
    lifecycle = AppLifecycleHandler("spatial-digital-twin", client=client)

    logger.info("Initializing WebGL Digital Shadow stream...")

    while lifecycle.is_running:
        if lifecycle.is_paused:
            time.sleep(1.0)
            continue

        telem = client.get_telemetry()
        soc = telem.get("battery_soc", 100.0)
        temp_coolant = telem.get("coolant_temp", 26.5)

        # Update 3D twin entities
        client.emit_telemetry(
            "spatial-digital-twin",
            {
                "rendered_fps": 60.0,
                "connected_viewers": 3,
                "active_meshes": 142,
                "coolant_celsius": temp_coolant,
                "battery_soc": soc,
            },
        )

        logger.info(
            "Digital Twin Stream Active | Connected Viewers: 3 | Coolant: %.1f°C | Battery: %.1f%%",
            temp_coolant,
            soc,
        )
        time.sleep(3.0)

    logger.info("Spatial 3D Digital Twin Engine shut down cleanly.")


if __name__ == "__main__":
    main()
