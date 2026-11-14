#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Terrestrial LoRa / Meshtastic Emergency Mesh Gateway
Bridges 868/915MHz off-grid field radios into BPv7 Space DTN bundles and Prometheus telemetry.

STATUS: the radio transport (serial / TCP Meshtastic link) is NOT implemented. The
packet codec and the DTN forwarding path are real; ``run_lora_daemon`` attaches no
radio, bridges nothing and reports no traffic counters.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any

logger = logging.getLogger("LoRaGateway")

SERIAL_PORT = os.getenv("LORA_SERIAL_PORT", "/dev/ttyUSB0")
BAUD_RATE = int(os.getenv("LORA_BAUD_RATE", "115200"))
TCP_HOST = os.getenv("LORA_TCP_HOST", "")
DTN_ENABLED = os.getenv("DTN_FALLBACK_ENABLED", "true").lower() == "true"
GATEWAY_EID = os.getenv("LORA_GATEWAY_EID", "dtn://lora-mesh-gw")
IDLE_POLL_SECONDS = 15.0
IDLE_REMINDER_EVERY = 240  # iterations (~1 h at the default poll interval)


def encode_packet(sender: str, receiver: str, payload: dict[str, Any]) -> bytes:
    """Encodes a JSON payload into a structured binary LoRa packet format."""
    obj = {"from": sender, "to": receiver, "ts": time.time(), "data": payload}
    return json.dumps(obj).encode("utf-8")


def decode_packet(raw_bytes: bytes) -> dict[str, Any]:
    """Decodes a raw binary packet into a dictionary structure."""
    try:
        decoded = json.loads(raw_bytes.decode("utf-8"))
    except Exception as e:
        return {"error": f"Corrupt packet: {e}", "raw": raw_bytes.hex()}
    if not isinstance(decoded, dict):
        return {"error": "Corrupt packet: top-level JSON value is not an object", "raw": raw_bytes.hex()}
    return decoded


_fallback_state_dir: str | None = None


def _local_default_spool_path() -> str:
    """``<state dir>/dtn_spool.db`` using only the standard library.

    Used when ``sovereign_dc`` is not importable (this file also runs on its own inside the
    LoRa container). Same rule as ``sovereign_dc.config.get_state_dir``: ``SMDC_STATE_DIR``
    or ``~/.smdc`` with mode 0700, never a predictable name in shared ``/tmp``; if that
    directory cannot be created, a fresh private ``mkdtemp()`` directory is used for the
    rest of the process.
    """
    global _fallback_state_dir
    state_dir = os.environ.get("SMDC_STATE_DIR") or os.path.expanduser("~/.smdc")
    try:
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        os.chmod(state_dir, 0o700)
    except OSError as err:
        if _fallback_state_dir is None:
            _fallback_state_dir = tempfile.mkdtemp(prefix="smdc-lora-")
            logger.warning(
                "State directory %s is unusable (%s); spooling to %s, which does NOT survive a reboot",
                state_dir,
                err,
                _fallback_state_dir,
            )
        state_dir = _fallback_state_dir
    return os.path.join(state_dir, "dtn_spool.db")


def resolve_dtn_spool_path() -> str:
    """Returns the DTN spool path: ``DTN_DB_PATH`` if set, else the private state-dir default.

    The environment variable is authoritative. Without it the default comes from
    ``sovereign_dc.space.dtn.router.default_spool_path`` (which also honours a trusted
    legacy ``/tmp/dtn_spool.db``) when that package is importable, else from
    ``_local_default_spool_path``.
    """
    env_path = os.getenv("DTN_DB_PATH")
    if env_path:
        return env_path
    try:
        from sovereign_dc.space.dtn.router import default_spool_path
    except ImportError:
        return _local_default_spool_path()
    return default_spool_path()


def forward_to_space_dtn(sender: str, message: str) -> bool:
    """Wraps an emergency LoRa message into a BPv7 bundle for orbital satellite relay.

    Returns True only if the bundle was actually written to the configured DTN spool.
    """
    logger.info("🛰️ Forwarding emergency LoRa message from '%s' to Space DTN relay...", sender)
    try:
        from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority
        from sovereign_dc.space.dtn.router import DTNRouter

        router = DTNRouter(db_path=resolve_dtn_spool_path(), local_node_eid=GATEWAY_EID)
        bundle = Bundle(
            source_eid=f"dtn://lora-mesh/{sender}",
            destination_eid="dtn://emergency-coordination.space/inbox",
            payload=message.encode("utf-8"),
            priority=BundlePriority.CRITICAL,
        )
        if not router.queue_bundle(bundle):
            logger.warning("DTN routing fallback notice: spool rejected bundle %s", bundle.bundle_id)
            return False
        logger.info("✅ Emergency bundle %s queued in space spool.", bundle.bundle_id)
        return True
    except Exception as e:
        logger.warning("DTN routing fallback notice: %s", e)
        return False


def run_lora_daemon(max_iterations: int | None = None) -> int:
    """Runs the gateway loop and returns the number of packets actually bridged.

    No radio transport is implemented, so nothing is received and nothing is bridged:
    the loop only keeps the service alive (so supervisors do not restart-loop) and
    periodically repeats that no radio is attached. ``max_iterations`` bounds the
    loop for tests; ``None`` runs forever.
    """
    logger.info("Starting Sovereign LoRa / Meshtastic Mesh Gateway (Port: %s)...", SERIAL_PORT)
    logger.error(
        "No LoRa radio attached: the serial/TCP Meshtastic transport is not implemented "
        "(configured port %s @ %d baud is never opened). 0 nodes discovered, 0 packets bridged.",
        SERIAL_PORT,
        BAUD_RATE,
    )
    packets_bridged = 0
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        if iteration % IDLE_REMINDER_EVERY == 0:
            logger.warning("[LoRa] Still no radio attached; packets bridged: %d", packets_bridged)
        time.sleep(IDLE_POLL_SECONDS)
    return packets_bridged


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [LoRaGateway] %(message)s")
    run_lora_daemon()
