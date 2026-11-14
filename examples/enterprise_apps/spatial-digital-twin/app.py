"""Spatial 3D Digital Twin Engine for Sovereign Mini Datacenter (SMDC).

Simulated reference skeleton: republishes the node's battery and coolant telemetry with
placeholder render figures (fixed constants). It renders nothing and serves no WebGL or
WebSocket stream.
"""

from __future__ import annotations

import logging
import time

from sovereign_dc.enterprise.sdk import AppLifecycleHandler, SMDCClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("spatial-digital-twin")


def main() -> None:
    logger.info("Starting Spatial 3D Digital Twin Engine skeleton (simulated: nothing is rendered)...")
    client = SMDCClient()
    lifecycle = AppLifecycleHandler("spatial-digital-twin", client=client)

    logger.info("No WebGL stream is opened; publishing placeholder render figures.")

    while lifecycle.is_running:
        # True only while an in-process EnterpriseManager has paused this app.
        if lifecycle.is_paused:
            time.sleep(1.0)
            continue

        telem = client.get_telemetry()
        # Flat SDK telemetry schema; missing readings are reported as NaN, never as "healthy".
        soc = float(telem.get("battery_soc") or 0.0)
        coolant_reading = telem.get("coolant_temp_c")
        temp_coolant = float(coolant_reading) if coolant_reading is not None else float("nan")

        # Update 3D twin entities
        client.emit_telemetry(
            "spatial-digital-twin",
            {
                "simulated": True,
                "rendered_fps": 60.0,
                "connected_viewers": 3,
                "active_meshes": 142,
                "coolant_celsius": temp_coolant,
                "battery_soc": soc,
            },
        )

        logger.info(
            "Digital twin skeleton (placeholder figures) | Viewers: 3 | Coolant: %.1f°C | Battery: %.1f%%",
            temp_coolant,
            soc,
        )
        time.sleep(3.0)

    logger.info("Spatial 3D Digital Twin Engine shut down cleanly.")


if __name__ == "__main__":
    main()
