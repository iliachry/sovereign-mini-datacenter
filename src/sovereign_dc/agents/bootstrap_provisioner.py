#!/usr/bin/env python3
"""
Sovereign Mini Datacenter — Autonomous Node Bootstrap Provisioner
Runs on node start-up as a read-only checker: it inventories the hardware, probes mesh
peers, checks whether Docker, Ollama, Qdrant and Prometheus answer, reads the DTN spool
depth, and reports the result (or what could not be verified) to the technician notifier.
It starts no service and synchronises no state; CRDT sync and backup verification are
reported as not implemented.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import platform
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sovereign_dc.agents.technician_notifier import (
    MessageSeverity,
    TechnicianNotifierChain,
)
from sovereign_dc.config import DEFAULT_NODE_ID, default_dtn_db_path, get_config
from sovereign_dc.hal.power import (
    METRIC_BATTERY_SOC,
    METRIC_SIMULATED,
    METRIC_SOLAR_WATTS,
    fetch_exporter_metrics,
    optional_metric,
)

logger = logging.getLogger("BootstrapProvisioner")


class BootstrapPhase(enum.IntEnum):
    """Enumeration of bootstrap provisioning phases."""

    IDLE = 0
    DISCOVERY = 1
    NETWORK = 2
    SERVICES = 3
    SYNC = 4
    READY = 5


STATUS_READY = "NODE_ONLINE_READY"
STATUS_DEGRADED = "NODE_ONLINE_DEGRADED"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

SOC_METRIC = METRIC_BATTERY_SOC
SOLAR_METRIC = METRIC_SOLAR_WATTS
SIMULATED_METRIC = METRIC_SIMULATED
LOW_BATTERY_SOC = 20.0  # default of SHEDDING_L3_SOC; the configured value is read by low_battery_soc()


def low_battery_soc() -> float:
    """SoC below which the node reports a low battery: the configured shedding level 3 threshold."""
    return float(get_config().shedding_l3_soc)


_PHASE_KEYS = ("discovery", "network", "services", "sync")


@dataclass
class BootstrapState:
    """Tracks state and results across all bootstrap provisioner phases."""

    node_id: str
    role: str
    start_time: float = field(default_factory=time.time)
    current_phase: int = 0
    phase_results: dict[str, Any] = field(default_factory=dict)
    hardware_info: dict[str, Any] = field(default_factory=dict)
    network_info: dict[str, Any] = field(default_factory=dict)
    services_info: dict[str, Any] = field(default_factory=dict)
    sync_info: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    is_complete: bool = False
    is_nominal: bool = True
    # Checks that failed or could not be performed. Any entry makes the node DEGRADED.
    unverified: list[str] = field(default_factory=list)
    dry_run: bool = False

    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time


class BootstrapProvisioner:
    """Autonomous bootstrap provisioner agent for Sovereign Mini Datacenter nodes.

    Every reported value is either measured or explicitly marked as unknown /
    unverified / not implemented; nothing is invented when a probe fails. With
    ``dry_run=True`` the same read-only probes run but there are no side effects: no
    technician notifications, no DTN bundles, no event-bus events, no files created.
    """

    def __init__(
        self,
        node_id: str | None = None,
        role: str | None = None,
        notifier_chain: TechnicianNotifierChain | None = None,
        dry_run: bool = False,
    ):
        self.node_id: str = str(node_id or os.getenv("NODE_ID") or DEFAULT_NODE_ID)
        self.role: str = str(role or os.getenv("NODE_ROLE") or "Primary Compute Core")
        self.dry_run = dry_run
        self.notifier = notifier_chain or TechnicianNotifierChain(node_id=self.node_id)
        self.state = BootstrapState(node_id=self.node_id, role=self.role, dry_run=dry_run)
        self._phase_unverified: dict[str, list[str]] = {}
        self._last_notified: dict[str, tuple[Any, ...]] = {}

    # ── Internal helpers ──────────────────────────────────────────────────
    def _notify(
        self,
        topic: str,
        signature: tuple[Any, ...],
        event_type: str,
        severity: MessageSeverity,
        message: str,
        details: dict[str, Any] | None = None,
        action_required: str | None = None,
    ) -> bool:
        """Dispatches a notification unless in dry-run or the state for ``topic`` is unchanged.

        ``signature`` captures the condition being reported; repeated health checks (the
        watchdog loop) only notify again when it changes. Returns True if dispatched.
        """
        if self.dry_run:
            logger.info("[dry-run] Notification suppressed: %s — %s", event_type, message)
            return False
        if self._last_notified.get(topic) == signature:
            logger.debug("State for '%s' unchanged; notification %s not repeated.", topic, event_type)
            return False
        self._last_notified[topic] = signature
        self.notifier.notify(
            event_type=event_type,
            severity=severity,
            message=message,
            details=details,
            action_required=action_required,
        )
        return True

    def _set_unverified(self, phase: str, items: list[str]) -> None:
        self._phase_unverified[phase] = items
        self.state.unverified = [item for key in _PHASE_KEYS for item in self._phase_unverified.get(key, [])]

    @staticmethod
    def _http_ok(url: str) -> bool:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "smdc-bootstrap"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                return bool(resp.status == 200)
        except Exception:
            return False

    @staticmethod
    def _read_power_telemetry(url: str) -> dict[str, Any]:
        """Reads battery / solar readings from the power exporter; absent keys mean unknown.

        This is the HAL's reader (``hal.power.fetch_exporter_metrics``), so bootstrap and the
        HAL cannot disagree about what a measurement is: an exporter that reports
        ``sovereign_telemetry_source_up 0`` has none, and a ``NaN`` or infinite sample is
        not a value. ``telemetry_available`` is True only with a finite battery SoC;
        otherwise ``error`` says why.
        """
        power: dict[str, Any] = {"telemetry_available": False, "source": url}
        try:
            metrics = fetch_exporter_metrics(url)
        except Exception as e:  # unreachable, source down (TelemetryUnavailable), unreadable body
            power["error"] = str(e)
            return power

        if METRIC_SIMULATED in metrics:
            power["simulated"] = metrics[METRIC_SIMULATED] > 0
        soc = optional_metric(metrics, METRIC_BATTERY_SOC)
        if soc is None:
            power["error"] = f"exporter reports no finite {METRIC_BATTERY_SOC}"
            return power
        power["battery_soc"] = soc
        solar = optional_metric(metrics, METRIC_SOLAR_WATTS)
        if solar is not None:
            power["solar_w"] = solar
        power["telemetry_available"] = True
        return power

    def _count_spool_bundles(self, spool_db: str) -> int | None:
        """Counts queued DTN bundles; None if the spool cannot be read. Never creates it in dry-run."""
        if not os.path.exists(spool_db):
            return 0
        try:
            if self.dry_run:
                conn = sqlite3.connect(f"file:{spool_db}?mode=ro", uri=True, timeout=2.0)
                try:
                    row = conn.execute("SELECT count(*) FROM outbound_spool WHERE status = 'QUEUED'").fetchone()
                finally:
                    conn.close()
                return int(row[0])
            from sovereign_dc.space.dtn.router import DTNRouter

            return int(DTNRouter(db_path=spool_db).get_queue_stats().get("queued_bundle_count", 0))
        except Exception as e:
            logger.warning("DTN spool at %s could not be read: %s", spool_db, e)
            return None

    # ── Phase 1: Self & Hardware Discovery ────────────────────────────────
    def phase_1_discovery(self) -> dict[str, Any]:
        """Detects host compute architecture, GPU accelerators, memory, storage, and power."""
        logger.info("Executing Phase 1: Self & Hardware Discovery for [%s]...", self.node_id)
        self.state.current_phase = 1
        unverified: list[str] = []

        info: dict[str, Any] = {
            "node_id": self.node_id,
            "role": self.role,
            "os": platform.system(),
            "release": platform.release(),
            "arch": platform.machine(),
            "cpu_count": os.cpu_count() or 1,
            "gpus": [],
            "gpu_count": 0,
            "gpu_detected": False,
            "storage": {},
            "power": {},
        }

        # 1. GPU Detection (nvidia-smi). No GPU is ever assumed.
        if shutil.which("nvidia-smi"):
            try:
                res = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0 and res.stdout.strip():
                    for line in res.stdout.strip().split("\n"):
                        parts = line.split(",")
                        name = parts[0].strip()
                        mem = parts[1].strip() if len(parts) > 1 else "Unknown"
                        info["gpus"].append({"name": name, "memory_mb": mem, "detected": True})
            except Exception as e:
                logger.warning("nvidia-smi query failed: %s", e)

        info["gpu_count"] = len(info["gpus"])
        info["gpu_detected"] = info["gpu_count"] > 0
        if not info["gpu_detected"]:
            # Explicit placeholder (not a device) so list consumers show the truth.
            info["gpus"].append(
                {"name": "No GPU detected", "memory_mb": None, "status": "not_detected", "detected": False}
            )
            unverified.append("gpu: no accelerator detected (nvidia-smi unavailable or returned nothing)")

        # 2. Disk & NVMe storage capacity
        try:
            total, used, free = shutil.disk_usage(os.getcwd())
            info["storage"] = {
                "total_gb": round(total / (1024**3), 1),
                "used_gb": round(used / (1024**3), 1),
                "free_gb": round(free / (1024**3), 1),
            }
        except Exception as e:
            info["storage"] = {"error": str(e)}
            unverified.append("storage: disk usage could not be read")

        # 3. Telemetry & Energy Check (power exporter). Unknown readings stay unknown.
        power_url = os.getenv("POWER_EXPORTER_URL", "http://localhost:9101/metrics")
        info["power"] = self._read_power_telemetry(power_url)
        soc: float | None = info["power"].get("battery_soc")
        if soc is None:
            unverified.append("power: battery telemetry unavailable")

        self.state.hardware_info = info
        self.state.phase_results["discovery"] = info
        self._set_unverified("discovery", unverified)

        # Technician notification on hardware health (only when the condition changes)
        gpu_text = f"{info['gpu_count']}x GPU"
        storage_text = f"{info['storage'].get('free_gb', 'unknown')}GB Free Storage"
        if soc is None:
            self._notify(
                "hardware",
                ("HARDWARE_UNVERIFIED", info["gpu_detected"]),
                event_type="HARDWARE_UNVERIFIED",
                severity=MessageSeverity.WARNING,
                message=f"Hardware discovered: {gpu_text}, {storage_text}. Battery/solar telemetry UNAVAILABLE.",
                details=info,
                action_required=f"Check the power telemetry exporter at {power_url}.",
            )
        elif soc < low_battery_soc():
            self._notify(
                "hardware",
                ("HARDWARE_WARNING", info["gpu_detected"]),
                event_type="HARDWARE_WARNING",
                severity=MessageSeverity.WARNING,
                message=f"Battery SoC is low ({soc:.1f}%). Node operating in energy preservation mode.",
                details=info,
                action_required="Ensure PV solar array is unshaded or connect auxiliary DC charge.",
            )
        else:
            simulated_text = " (SIMULATED telemetry)" if info["power"].get("simulated") else ""
            self._notify(
                "hardware",
                ("HARDWARE_OK", info["gpu_detected"], bool(info["power"].get("simulated"))),
                event_type="HARDWARE_OK",
                severity=MessageSeverity.INFO,
                message=f"Hardware discovered: {gpu_text}, {storage_text}, {soc:.1f}% Battery SoC{simulated_text}.",
                details=info,
            )

        return info

    # ── Phase 2: Mesh & Multi-Tier Network Bootstrap ─────────────────────
    def phase_2_network(self) -> dict[str, Any]:
        """Evaluates multi-tier network connectivity by probing mesh peers."""
        logger.info("Executing Phase 2: Mesh & Network Bootstrap for [%s]...", self.node_id)
        self.state.current_phase = 2
        unverified: list[str] = []

        net_info: dict[str, Any] = {
            "tier1_wireguard": False,
            "tier2_starlink": False,
            "tier2_starlink_status": NOT_IMPLEMENTED,
            "tier3_lora": False,
            "tier3_lora_status": NOT_IMPLEMENTED,
            "tier4_space_dtn": False,
            "active_peers": [],
            "unreachable_peers": [],
        }

        from sovereign_dc.mesh.mesh_sync import check_peer_health, load_peers, remote_peers

        # Only nodes other than this one count, and only if they answered a probe.
        for peer in remote_peers(load_peers(), self.node_id):
            if check_peer_health(peer):
                net_info["active_peers"].append(peer.node_id)
            else:
                net_info["unreachable_peers"].append(peer.node_id)

        # WireGuard link status is inferred from peer reachability over the overlay: it is
        # confirmed only by a remote peer's answer.
        net_info["tier1_wireguard"] = len(net_info["active_peers"]) > 0
        if not net_info["tier1_wireguard"]:
            unverified.append("network: no mesh peer reachable over WireGuard")

        # Space DTN store-and-forward is "armed" when its spool location is writable.
        spool_db = os.getenv("DTN_DB_PATH") or default_dtn_db_path()
        spool_dir = os.path.dirname(os.path.abspath(spool_db))
        net_info["tier4_space_dtn"] = os.path.isdir(spool_dir) and os.access(spool_dir, os.W_OK)
        if not net_info["tier4_space_dtn"]:
            unverified.append("network: DTN spool directory is not writable")

        self.state.network_info = net_info
        self.state.phase_results["network"] = net_info
        self._set_unverified("network", unverified)

        signature = (net_info["tier1_wireguard"], tuple(sorted(net_info["active_peers"])))
        if net_info["tier1_wireguard"]:
            self._notify(
                "network",
                signature,
                event_type="NETWORK_UP",
                severity=MessageSeverity.INFO,
                message=f"WireGuard mesh established. {len(net_info['active_peers'])} peer(s) reachable.",
                details=net_info,
            )
        else:
            self._notify(
                "network",
                signature,
                event_type="NETWORK_DEGRADED",
                severity=MessageSeverity.WARNING,
                message="Terrestrial WireGuard link unconfirmed: no mesh peer reachable. "
                "Only the Space DTN store-and-forward spool is available as fallback.",
                details=net_info,
                action_required="Verify MikroTik CRS309 switch uplink or Headscale auth key if remote.",
            )

        return net_info

    # ── Phase 3: Service Provisioning & Container Orchestration ──────────
    def phase_3_services(self) -> dict[str, Any]:
        """Checks the container engine and core AI services. Read-only: starts nothing."""
        logger.info("Executing Phase 3: Service Provisioning for [%s]...", self.node_id)
        self.state.current_phase = 3
        unverified: list[str] = []

        srv_info: dict[str, Any] = {
            "docker_available": False,
            "ollama_ready": False,
            "qdrant_ready": False,
            "prometheus_ready": False,
            # Names of containers actually observed running (``docker ps``); never assumed.
            "services_started": [],
        }

        # 1. Check Docker runtime and list running containers (both read-only)
        if shutil.which("docker"):
            try:
                res = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
                srv_info["docker_available"] = res.returncode == 0
                if srv_info["docker_available"]:
                    ps = subprocess.run(
                        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=5
                    )
                    if ps.returncode == 0:
                        srv_info["services_started"] = sorted(n for n in ps.stdout.split("\n") if n.strip())
            except Exception as e:
                logger.warning("Docker check failed: %s", e)
        if not srv_info["docker_available"]:
            unverified.append("services: docker runtime unavailable")

        # 2-4. Check local Ollama AI engine, Qdrant vector database and Prometheus
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        qdrant_url = os.getenv("QDRANT_BASE_URL", "http://localhost:6333")
        prometheus_url = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
        srv_info["ollama_ready"] = self._http_ok(f"{ollama_url}/api/tags")
        srv_info["qdrant_ready"] = self._http_ok(f"{qdrant_url}/collections")
        srv_info["prometheus_ready"] = self._http_ok(f"{prometheus_url}/-/ready")
        for key, label in (("ollama_ready", "ollama"), ("qdrant_ready", "qdrant"), ("prometheus_ready", "prometheus")):
            if not srv_info[key]:
                unverified.append(f"services: {label} not reachable")

        self.state.services_info = srv_info
        self.state.phase_results["services"] = srv_info
        self._set_unverified("services", unverified)

        all_ready = not unverified
        status_text = (
            f"Docker={'ONLINE' if srv_info['docker_available'] else 'UNAVAILABLE'}, "
            f"Ollama={'ONLINE' if srv_info['ollama_ready'] else 'UNREACHABLE'}, "
            f"Qdrant={'ONLINE' if srv_info['qdrant_ready'] else 'UNREACHABLE'}, "
            f"Prometheus={'ONLINE' if srv_info['prometheus_ready'] else 'UNREACHABLE'}"
        )
        self._notify(
            "services",
            (all_ready, status_text),
            event_type="SERVICES_READY" if all_ready else "SERVICES_DEGRADED",
            severity=MessageSeverity.INFO if all_ready else MessageSeverity.WARNING,
            message=f"Core services check: {status_text}.",
            details=srv_info,
            action_required=None if all_ready else "Start the core compose stack (software/setup.sh).",
        )

        return srv_info

    # ── Phase 4: State Synchronization & Disaster Recovery ───────────────
    def phase_4_sync(self) -> dict[str, Any]:
        """Reports the Space DTN spool depth.

        CRDT state synchronisation and backup verification are not implemented; they
        are reported as such rather than as successes.
        """
        logger.info("Executing Phase 4: State Sync & Recovery for [%s]...", self.node_id)
        self.state.current_phase = 4
        unverified: list[str] = []

        spool_db = os.getenv("DTN_DB_PATH") or default_dtn_db_path()
        bundles = self._count_spool_bundles(spool_db)
        sync_info: dict[str, Any] = {
            "crdt_sync": NOT_IMPLEMENTED,
            "dtn_spool_bundles": bundles if bundles is not None else 0,
            "dtn_spool_readable": bundles is not None,
            "backup_verified": None,
            "backup_status": NOT_IMPLEMENTED,
            "not_implemented": ["crdt_sync", "backup_verification"],
        }
        if bundles is None:
            unverified.append("sync: DTN spool could not be read")

        self.state.sync_info = sync_info
        self.state.phase_results["sync"] = sync_info
        self._set_unverified("sync", unverified)

        spool_text = f"{bundles} bundle(s)" if bundles is not None else "an UNKNOWN number of bundles (unreadable)"
        self._notify(
            "sync",
            (bundles,),
            event_type="SYNC_STATUS",
            severity=MessageSeverity.INFO if bundles is not None else MessageSeverity.WARNING,
            message=f"DTN spool queue holds {spool_text}. "
            "CRDT state sync and backup verification are not implemented and were NOT performed.",
            details=sync_info,
        )

        return sync_info

    # ── Phase 5: Autonomous Ready & Technician Reporting ──────────────────
    def phase_5_ready(self) -> dict[str, Any]:
        """Marks bootstrap complete and emits the operational readiness attestation.

        The node is NODE_ONLINE_READY only if every phase ran, no error occurred and no
        check failed or was left unverified; otherwise it is NODE_ONLINE_DEGRADED.
        """
        logger.info("Executing Phase 5: Final Readiness & Attestation for [%s]...", self.node_id)
        self.state.current_phase = 5

        skipped = [f"phase '{key}' was not executed" for key in _PHASE_KEYS if key not in self.state.phase_results]
        unverified = [*self.state.unverified, *skipped]
        self.state.is_complete = True
        self.state.is_nominal = not self.state.errors and not unverified
        status = STATUS_READY if self.state.is_nominal else STATUS_DEGRADED

        summary: dict[str, Any] = {
            "node_id": self.node_id,
            "role": self.role,
            "status": status,
            "timestamp": datetime.now(UTC).isoformat(),
            "elapsed_seconds": round(self.state.elapsed_seconds(), 2),
            "phases_completed": len(_PHASE_KEYS) - len(skipped) + 1,
            "hardware": self.state.hardware_info,
            "network": self.state.network_info,
            "services": self.state.services_info,
            "sync": self.state.sync_info,
            "errors": self.state.errors,
            "unverified": unverified,
            "not_implemented": list(self.state.sync_info.get("not_implemented", [])),
            "dry_run": self.dry_run,
        }

        self.state.phase_results["ready"] = summary

        if self.state.is_nominal:
            message = (
                f"Sovereign node {self.node_id} is operational and joined to mesh in {summary['elapsed_seconds']}s."
            )
        else:
            message = (
                f"Sovereign node {self.node_id} is online but DEGRADED: "
                f"{len(self.state.errors)} error(s), {len(unverified)} failed/unverified check(s)."
            )
        self._notify(
            "ready",
            (status, tuple(unverified), tuple(self.state.errors)),
            event_type=status,
            severity=MessageSeverity.INFO if self.state.is_nominal else MessageSeverity.WARNING,
            message=message,
            details=summary,
            action_required=None if self.state.is_nominal else "Review the 'unverified' and 'errors' lists.",
        )

        if not self.dry_run:
            try:
                from sovereign_dc.events import Event, EventType, get_event_bus

                get_event_bus().publish(
                    Event(
                        event_type=EventType.NODE_ONLINE if self.state.is_nominal else EventType.NODE_DEGRADED,
                        source=self.node_id,
                        payload=summary,
                    )
                )
            except Exception as e:
                logger.debug("Event bus publication notice: %s", e)

        return summary

    def run_all_phases(self) -> BootstrapState:
        """Executes full 5-phase autonomous bootstrap pipeline."""
        logger.info("Starting Autonomous Bootstrap Sequence for node [%s]...", self.node_id)

        self._notify(
            "boot",
            ("BOOT_STARTED",),
            event_type="BOOT_STARTED",
            severity=MessageSeverity.INFO,
            message=f"Autonomous bootstrap sequence initiated on power-up for {self.node_id} ({self.role}).",
        )

        try:
            self.phase_1_discovery()
            self.phase_2_network()
            self.phase_3_services()
            self.phase_4_sync()
            self.phase_5_ready()
        except Exception as e:
            logger.error("Bootstrap sequence halted due to critical error: %s", e)
            self.state.errors.append(str(e))
            self.state.is_nominal = False
            if self.dry_run:
                logger.info("[dry-run] Human-help request suppressed for bootstrap failure: %s", e)
            else:
                self.notifier.request_human_help(
                    issue_title=f"Bootstrap failure on {self.node_id}",
                    remediation_step=f"Inspect node terminal logs or restart bootstrap with 'smdc bootstrap'. Error: {e}",
                    details={"error": str(e), "current_phase": self.state.current_phase},
                )

        return self.state


def run_bootstrap_daemon(poll_interval_seconds: int = 120) -> None:
    """Runs bootstrap provisioner on boot, then transitions into a persistent health watchdog.

    The watchdog re-runs the hardware and network checks every interval; technicians are
    notified only when the observed state changes, never on every poll.
    """
    provisioner = BootstrapProvisioner()
    provisioner.run_all_phases()

    logger.info(
        "Bootstrap complete. Node entering continuous health watchdog loop (interval: %ds)...", poll_interval_seconds
    )
    while True:
        try:
            # Periodically re-verify power and network status
            provisioner.phase_1_discovery()
            provisioner.phase_2_network()
        except Exception as e:
            logger.warning("Watchdog health check notice: %s", e)
        time.sleep(poll_interval_seconds)


if __name__ == "__main__":
    provisioner = BootstrapProvisioner()
    res = provisioner.run_all_phases()
    print(json.dumps(res.phase_results, indent=2))
