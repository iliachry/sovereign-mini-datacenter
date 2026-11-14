#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Mesh Peer Reachability Monitor
Loads the peer list from the cluster topology file and periodically probes each peer's
telemetry exporter over its mesh (WireGuard) address, logging which peers answer.

It transfers no data: there is no state, repository, backup or DTN bundle synchronisation
here (the file keeps its historical name, mesh_sync.py).
"""

import logging
import math
import os
import time
import urllib.error
import urllib.request
from typing import Any

# Logging is configured only when this file runs as a script (see the bottom of the file):
# importing it as a library must not touch the root logger.
logger = logging.getLogger("MeshSync")
LOG_FORMAT = "%(asctime)s [MeshSync] %(message)s"

NODE_ID = os.getenv("NODE_ID", "smdc-node-01")
# Cluster topology file. Empty means "cluster_config.yaml next to this file, if present"
# (see cluster_config_path()); the standalone copy in software/mesh/ ships one.
CLUSTER_CONFIG_PATH = os.getenv("CLUSTER_CONFIG_PATH", "")
DEFAULT_CLUSTER_CONFIG_FILENAME = "cluster_config.yaml"
# Seconds between peer health sweeps: MESH_POLL_INTERVAL_SECONDS (default 60, at least 1).
DEFAULT_POLL_INTERVAL_SECONDS = 60.0
MIN_POLL_INTERVAL_SECONDS = 1.0


class MeshNode:
    def __init__(self, node_id: str, wireguard_ip: str, dtn_eid: str, role: str) -> None:
        self.node_id = node_id
        self.wireguard_ip = wireguard_ip
        self.dtn_eid = dtn_eid
        self.role = role
        self.is_online = False


def default_peers() -> list[MeshNode]:
    """The built-in three-site reference topology, used when no cluster file is available."""
    return [
        MeshNode("smdc-node-01", "100.64.0.1", "dtn://smdc-node-01.sovereign.space", "Primary Core"),
        MeshNode("smdc-node-02", "100.64.0.2", "dtn://smdc-node-02.sovereign.space", "Edge Satellite Node"),
        MeshNode("smdc-node-03", "100.64.0.3", "dtn://smdc-node-03.sovereign.space", "Off-Grid Island Node"),
    ]


def cluster_config_path() -> str:
    """Path of the cluster topology file.

    ``CLUSTER_CONFIG_PATH`` if set (read at call time), else ``cluster_config.yaml`` in the
    directory of this file. The default is never resolved against the working directory.
    """
    configured = os.getenv("CLUSTER_CONFIG_PATH") or CLUSTER_CONFIG_PATH
    if configured:
        return configured
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), DEFAULT_CLUSTER_CONFIG_FILENAME)


def _parse_peers(data: Any) -> list[MeshNode]:
    """Builds peers from a parsed cluster file; entries without ``id``/``wireguard_ip`` are skipped."""
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        raise ValueError("no 'nodes' list")
    peers: list[MeshNode] = []
    for entry in nodes:
        node_id = entry.get("id") if isinstance(entry, dict) else None
        wireguard_ip = entry.get("wireguard_ip") if isinstance(entry, dict) else None
        if not isinstance(node_id, str) or not node_id or not isinstance(wireguard_ip, str) or not wireguard_ip:
            logger.warning("Skipping cluster node entry without a string 'id' and 'wireguard_ip': %r", entry)
            continue
        dtn_eid = entry.get("dtn_eid")
        role = entry.get("role")
        peers.append(
            MeshNode(
                node_id,
                wireguard_ip,
                dtn_eid if isinstance(dtn_eid, str) and dtn_eid else f"dtn://{node_id}.sovereign.space",
                role if isinstance(role, str) and role else "unknown",
            )
        )
    if not peers:
        raise ValueError("no usable node entries")
    return peers


def load_peers() -> list[MeshNode]:
    """Loads peer nodes from the cluster topology file (see ``cluster_config_path``).

    Falls back to ``default_peers()`` when the file is absent, unreadable, has no usable
    node entries, or PyYAML is not installed. An explicitly configured path that cannot be
    used is logged as a warning; a missing default file is not.
    """
    path = cluster_config_path()
    explicit = bool(os.getenv("CLUSTER_CONFIG_PATH") or CLUSTER_CONFIG_PATH)
    if not os.path.isfile(path):
        if explicit:
            logger.warning("Cluster config %s not found; using the built-in reference topology.", path)
        return default_peers()
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            peers = _parse_peers(yaml.safe_load(f))
    except ImportError:
        logger.warning("PyYAML is not installed; cannot read %s. Using the built-in reference topology.", path)
        return default_peers()
    except Exception as e:
        logger.warning("Cluster config %s is unusable (%s); using the built-in reference topology.", path, e)
        return default_peers()
    logger.info("Loaded %d mesh peer(s) from %s.", len(peers), path)
    return peers


def poll_interval_seconds(raw: str | None = None) -> float:
    """Seconds between peer health sweeps, from ``MESH_POLL_INTERVAL_SECONDS`` (read at call time).

    Default 60. A value below 1 is raised to 1; a value that is not a finite number is
    ignored (with a warning) in favour of the default.
    """
    if raw is None:
        raw = os.getenv("MESH_POLL_INTERVAL_SECONDS", "")
    raw = raw.strip()
    if not raw:
        return DEFAULT_POLL_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        logger.warning(
            "MESH_POLL_INTERVAL_SECONDS=%r is not a finite number; using %.0f s.", raw, DEFAULT_POLL_INTERVAL_SECONDS
        )
        return DEFAULT_POLL_INTERVAL_SECONDS
    return max(MIN_POLL_INTERVAL_SECONDS, value)


def check_peer_health(peer: MeshNode) -> bool:
    """True if the peer's exporter answers HTTP 200 on ``http://<wireguard_ip>:9101/metrics`` within 2 s.

    The local node is always reported reachable without a request.
    """
    if peer.node_id == NODE_ID:
        return True
    try:
        url = f"http://{peer.wireguard_ip}:9101/metrics"
        req = urllib.request.Request(url, headers={"User-Agent": "smdc-mesh"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            return bool(resp.status == 200)
    except Exception:
        return False


def log_peer_reachable(peer: MeshNode) -> None:
    """Logs that a peer answered the reachability probe. Nothing is synchronised with it."""
    logger.info("Mesh peer '%s' (%s) is reachable.", peer.node_id, peer.wireguard_ip)


def run_mesh_daemon() -> None:
    interval = poll_interval_seconds()
    logger.info("Starting mesh peer reachability monitor for [%s] (sweep every %.0f s)...", NODE_ID, interval)
    peers = load_peers()

    while True:
        for p in peers:
            if p.node_id != NODE_ID:
                p.is_online = check_peer_health(p)
                if p.is_online:
                    log_peer_reachable(p)
                else:
                    logger.warning("Mesh peer '%s' (%s) is unreachable.", p.node_id, p.wireguard_ip)
        time.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    run_mesh_daemon()
