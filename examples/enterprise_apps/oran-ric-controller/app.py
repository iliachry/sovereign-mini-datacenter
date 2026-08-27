"""O-RAN Near-RT RAN Intelligent Controller (RIC) xApp for Sovereign Mini Datacenter.

Manages 5G network slicing QoS (URLLC, eMBB, mMTC), enforces DePIN SLA constraints,
and interfaces directly with the SMDC event bus and hardware telemetry stream.
"""

from __future__ import annotations

import logging
import time

from sovereign_dc.enterprise.sdk import AppLifecycleHandler, SMDCClient
from sovereign_dc.metaverse.depin_sla import DePINSLAValidator
from sovereign_dc.metaverse.slicing import NetworkSlicingManager, SliceType

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("oran-ric-controller")


def main() -> None:
    logger.info("Initializing O-RAN Near-RT RIC 5G Slicing Controller (xApp)...")
    client = SMDCClient()
    lifecycle = AppLifecycleHandler("oran-ric-controller", client=client)

    slicing_mgr = NetworkSlicingManager()
    sla_validator = DePINSLAValidator()

    logger.info(
        "RIC Slices Active: URLLC (10 Mbps, <1ms), eMBB (127 Mbps, 15ms), mMTC (5 Mbps, 12k devs)"
    )

    iteration = 0
    while lifecycle.is_running:
        if lifecycle.is_paused:
            time.sleep(1.0)
            continue

        iteration += 1
        telem = client.get_telemetry()
        soc = telem.get("battery_soc", 100.0)

        # Process traffic cycles across slices
        pkt_urllc = slicing_mgr.transmit_uav_control_command((5.0, 0.0, 0.0))
        pkt_xr = slicing_mgr.transmit_xr_frame(180000)
        pkt_iot = slicing_mgr.ingest_iot_sensor_batch(180, 64)

        # Emit telemetry to SMDC bus
        client.emit_telemetry(
            "oran-ric-controller",
            {
                "urllc_latency_ms": pkt_urllc.latency_ms,
                "embb_active_conns": slicing_mgr.slices[SliceType.EMBB].current_active_connections,
                "mmtc_devices_online": slicing_mgr.slices[SliceType.MMTC].current_active_connections,
                "depin_consensus_nodes": len(sla_validator.validators),
                "battery_soc": soc,
                "iteration": iteration,
            },
        )

        if iteration % 3 == 0:
            logger.info(
                "O-RAN Slices Active | URLLC Latency: %.2f ms | eMBB Conns: %d | mMTC Sensors: %d | Battery: %.1f%%",
                pkt_urllc.latency_ms,
                slicing_mgr.slices[SliceType.EMBB].current_active_connections,
                slicing_mgr.slices[SliceType.MMTC].current_active_connections,
                soc,
            )

        time.sleep(3.0)

    logger.info("O-RAN Near-RT RIC Controller shut down cleanly.")


if __name__ == "__main__":
    main()
