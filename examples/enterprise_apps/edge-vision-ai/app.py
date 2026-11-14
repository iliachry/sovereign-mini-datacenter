"""Edge Vision AI Pipeline for Sovereign Mini Datacenter (SMDC).

Demonstrates a simulated video/image batch inference loop, the pause/resume hooks of an
$L_2$ background-tier workload, and Space DTN event dispatch.
"""

from __future__ import annotations

import logging
import random
import time

from sovereign_dc.enterprise.sdk import AppLifecycleHandler, SMDCClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("edge-vision-ai")


def on_power_pause() -> None:
    logger.warning("Pause requested by the SMDC enterprise manager. Halting GPU vision inference pipelines...")


def on_power_resume() -> None:
    logger.info("Resume requested by the SMDC enterprise manager. Resuming GPU batch inference...")


def main() -> None:
    logger.info("Initializing Edge Vision AI Model Engine...")
    client = SMDCClient()
    lifecycle = AppLifecycleHandler(
        "edge-vision-ai",
        client=client,
        on_pause=on_power_pause,
        on_resume=on_power_resume,
    )

    frame_count = 0
    while lifecycle.is_running:
        # is_paused follows the EnterpriseManager's pause/resume events for this app. They are
        # delivered over the in-process event bus, i.e. only when this workload shares a
        # Python process with the manager (see README).
        if lifecycle.is_paused:
            time.sleep(1.0)
            continue

        frame_count += 10

        # Simulate camera frame batch inference
        latency_ms = random.uniform(12.5, 24.8)
        detections = random.randint(1, 5)

        # Emit inference metrics
        client.emit_telemetry(
            "edge-vision-ai",
            {
                "frames_processed": frame_count,
                "latency_ms": round(latency_ms, 2),
                "fps": round(1000.0 / latency_ms, 1),
                "detections_count": detections,
            },
        )

        logger.info(
            "Batch processed: %d frames | Latency: %.1f ms (%.1f FPS) | Detections: %d",
            frame_count,
            latency_ms,
            1000.0 / latency_ms,
            detections,
        )

        # Critical anomaly detected -> spool Space DTN bundle if required
        if random.random() < 0.05:
            logger.info("High-priority visual anomaly detected! Spooling RFC 9171 Space DTN alert...")
            client.send_dtn_bundle(
                destination="dtn://hq.earth/security/anomalies",
                payload_data=f"Anomaly: frame={frame_count} detections={detections}",
            )

        time.sleep(2.0)

    logger.info("Edge Vision AI Pipeline shut down cleanly.")


if __name__ == "__main__":
    main()
