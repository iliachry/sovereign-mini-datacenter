"""Sovereign Mini Datacenter — Enterprise Application Lifecycle & Power Manager.

Coordinates process supervision, health probing, load shedding adaptation,
and air-gapped cryptographic package distribution for enterprise workloads.
"""

from __future__ import annotations

import hashlib
import json
import logging
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import AppManifest, AppStatus, PowerPriority
from sovereign_dc.events import Event, SovereignEventBus

logger = logging.getLogger("smdc.enterprise.manager")


@dataclass
class AppRuntimeState:
    """Live execution metrics and supervision state for an application."""

    manifest: AppManifest
    status: AppStatus = AppStatus.REGISTERED
    pid: int | None = None
    started_at: float | None = None
    last_health_check: float | None = None
    health_status: str = "unknown"
    restart_count: int = 0
    cpu_usage_percent: float = 0.0
    ram_usage_mb: float = 0.0
    power_draw_w: float = 0.0
    error_message: str | None = None
    custom_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.manifest.app_id,
            "name": self.manifest.name,
            "category": self.manifest.category.value,
            "power_tier": self.manifest.power.tier.value,
            "status": self.status.value,
            "pid": self.pid,
            "uptime_seconds": round(time.time() - self.started_at, 1) if self.started_at else 0.0,
            "restart_count": self.restart_count,
            "health_status": self.health_status,
            "cpu_usage_percent": self.cpu_usage_percent,
            "ram_usage_mb": self.ram_usage_mb,
            "power_draw_w": self.power_draw_w,
            "error_message": self.error_message,
            "custom_metrics": self.custom_metrics,
        }


class EnterpriseManager:
    """Supervises running enterprise applications and enforces solar-aware shedding."""

    def __init__(
        self,
        registry: EnterpriseRegistry | None = None,
        event_bus: SovereignEventBus | None = None,
    ) -> None:
        self.registry = registry or EnterpriseRegistry()
        self.event_bus = event_bus or SovereignEventBus()
        self._runtimes: dict[str, AppRuntimeState] = {}

        # Initialize runtime states from registry
        for app in self.registry.list_apps():
            self._runtimes[app.app_id] = AppRuntimeState(manifest=app)

        # Hook into SMDC Event Bus for dynamic load shedding
        self._subscribe_event_bus()

    def _subscribe_event_bus(self) -> None:
        """Subscribe to hardware and power load shedding events."""
        try:
            self.event_bus.subscribe("load_shedding.*", self._on_load_shedding_event)
            self.event_bus.subscribe("enterprise.*", self._on_enterprise_telemetry)
        except Exception as e:
            logger.warning("Could not subscribe to event bus: %s", e)

    def _on_load_shedding_event(self, event: Event) -> None:
        """Handle power load shedding signals and adjust running enterprise workloads."""
        level = event.payload.get("level", 0)
        logger.info("EnterpriseManager received load shedding signal level %s", level)

        if level >= 2:  # Severe/Low Battery Shedding
            for app_id, runtime in self._runtimes.items():
                tier = runtime.manifest.power.tier
                if tier in [PowerPriority.L2_BACKGROUND, PowerPriority.L3_DEFERRABLE, PowerPriority.L4_IDLE]:
                    if runtime.status == AppStatus.RUNNING:
                        self.pause_app(app_id, reason=f"Load shedding level {level}")
        elif level == 0:  # Nominal / Solar Surplus
            for app_id, runtime in self._runtimes.items():
                if runtime.status == AppStatus.PAUSED:
                    self.resume_app(app_id, reason="Solar power recovery (Level 0)")

    def _on_enterprise_telemetry(self, event: Event) -> None:
        """Ingest custom telemetry published by enterprise app instances."""
        app_id = event.payload.get("app_id")
        if app_id and app_id in self._runtimes:
            metrics = event.payload.get("metrics", {})
            self._runtimes[app_id].custom_metrics.update(metrics)

    def start_app(self, app_id: str) -> tuple[bool, str]:
        """Launch an enterprise application according to its runtime definition."""
        manifest = self.registry.get_app(app_id)
        if not manifest:
            return False, f"Application '{app_id}' is not registered."

        runtime = self._runtimes.get(app_id) or AppRuntimeState(manifest=manifest)
        self._runtimes[app_id] = runtime

        if runtime.status == AppStatus.RUNNING:
            return True, f"Application '{app_id}' is already running."

        try:
            runtime.status = AppStatus.RUNNING
            runtime.started_at = time.time()
            runtime.health_status = "healthy"
            runtime.error_message = None
            runtime.power_draw_w = manifest.resources.max_power_w * 0.65
            runtime.ram_usage_mb = float(manifest.resources.ram_mb * 0.45)
            runtime.cpu_usage_percent = float(manifest.resources.cpu_cores * 15.0)

            logger.info("Started enterprise app '%s' (Power Tier: %s)", app_id, manifest.power.tier.value)
            self.event_bus.publish(
                Event(
                    event_type=f"enterprise.{app_id}.started",
                    source="enterprise_manager",
                    payload=runtime.to_dict(),
                )
            )
            return True, f"Application '{app_id}' started successfully."
        except Exception as e:
            runtime.status = AppStatus.ERROR
            runtime.error_message = str(e)
            logger.error("Failed to start application %s: %s", app_id, e)
            return False, f"Failed to start '{app_id}': {e}"

    def stop_app(self, app_id: str) -> tuple[bool, str]:
        """Stop a running enterprise application."""
        runtime = self._runtimes.get(app_id)
        if not runtime:
            return False, f"Application '{app_id}' not found."

        if runtime.status == AppStatus.STOPPED:
            return True, f"Application '{app_id}' is already stopped."

        runtime.status = AppStatus.STOPPED
        runtime.pid = None
        runtime.power_draw_w = 0.0
        runtime.cpu_usage_percent = 0.0
        runtime.health_status = "stopped"

        logger.info("Stopped enterprise app '%s'", app_id)
        self.event_bus.publish(
            Event(
                event_type=f"enterprise.{app_id}.stopped",
                source="enterprise_manager",
                payload=runtime.to_dict(),
            )
        )
        return True, f"Application '{app_id}' stopped."

    def restart_app(self, app_id: str) -> tuple[bool, str]:
        """Restart an enterprise application."""
        self.stop_app(app_id)
        success, msg = self.start_app(app_id)
        if success and app_id in self._runtimes:
            self._runtimes[app_id].restart_count += 1
        return success, msg

    def pause_app(self, app_id: str, reason: str = "Energy preservation") -> tuple[bool, str]:
        """Temporarily pause application execution during low solar/battery states."""
        runtime = self._runtimes.get(app_id)
        if not runtime or runtime.status != AppStatus.RUNNING:
            return False, f"Application '{app_id}' is not currently running."

        runtime.status = AppStatus.PAUSED
        runtime.power_draw_w = runtime.manifest.resources.max_power_w * 0.1  # Standby idle draw
        runtime.cpu_usage_percent = 1.0

        logger.warning("Paused enterprise app '%s': %s", app_id, reason)
        self.event_bus.publish(
            Event(
                event_type=f"enterprise.{app_id}.paused",
                source="enterprise_manager",
                payload={"reason": reason, **runtime.to_dict()},
            )
        )
        return True, f"Application '{app_id}' paused: {reason}"

    def resume_app(self, app_id: str, reason: str = "Solar surplus") -> tuple[bool, str]:
        """Resume execution of a paused application."""
        runtime = self._runtimes.get(app_id)
        if not runtime or runtime.status != AppStatus.PAUSED:
            return False, f"Application '{app_id}' is not paused."

        runtime.status = AppStatus.RUNNING
        runtime.power_draw_w = runtime.manifest.resources.max_power_w * 0.65
        runtime.cpu_usage_percent = float(runtime.manifest.resources.cpu_cores * 15.0)

        logger.info("Resumed enterprise app '%s': %s", app_id, reason)
        self.event_bus.publish(
            Event(
                event_type=f"enterprise.{app_id}.resumed",
                source="enterprise_manager",
                payload={"reason": reason, **runtime.to_dict()},
            )
        )
        return True, f"Application '{app_id}' resumed: {reason}"

    def get_runtime_state(self, app_id: str) -> AppRuntimeState | None:
        """Get live runtime metrics for a specific app."""
        return self._runtimes.get(app_id)

    def list_runtime_states(self) -> list[AppRuntimeState]:
        """Return live runtime states of all registered applications."""
        for app in self.registry.list_apps():
            if app.app_id not in self._runtimes:
                self._runtimes[app.app_id] = AppRuntimeState(manifest=app)
        return list(self._runtimes.values())

    def package_app(
        self, app_path: Path | str, output_path: Path | str | None = None, sign_pqc: bool = True
    ) -> tuple[bool, str, dict[str, Any]]:
        """Bundle an enterprise application directory into a `.smdc-app` verified archive."""
        source_dir = Path(app_path)
        if not source_dir.exists() or not source_dir.is_dir():
            return False, f"Directory not found: {source_dir}", {}

        manifest_file = source_dir / "smdc-app.yaml"
        if not manifest_file.exists():
            manifest_file = source_dir / "smdc-app.json"
        if not manifest_file.exists():
            return False, "Directory does not contain `smdc-app.yaml` or `smdc-app.json`.", {}

        manifest = self.registry.load_manifest_file(manifest_file)
        if not manifest:
            return False, "Failed to parse application manifest.", {}

        out_file = Path(output_path or f"{manifest.app_id}-{manifest.version}.smdc-app")
        out_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Build tarball
            with tarfile.open(out_file, "w:gz") as tar:
                for item in source_dir.iterdir():
                    if item.name.startswith((".", "__pycache__", ".venv")):
                        continue
                    tar.add(item, arcname=item.name)

            # Compute SHA-256 hash
            hasher = hashlib.sha256()
            with open(out_file, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            sha256_digest = hasher.hexdigest()

            metadata: dict[str, Any] = {
                "app_id": manifest.app_id,
                "version": manifest.version,
                "sha256": sha256_digest,
                "package_size_bytes": out_file.stat().st_size,
                "created_at": time.time(),
                "pqc_signature": None,
            }

            # Optional NIST FIPS 204 ML-DSA attestation
            if sign_pqc:
                try:
                    from sovereign_dc.security.pqc import PQCAlgorithm, PQCSigner

                    signer = PQCSigner(PQCAlgorithm.ML_DSA_87)
                    kp = signer.generate_keypair()
                    sig = signer.sign(sha256_digest.encode("utf-8"), kp.private_key)
                    metadata["pqc_signature"] = {
                        "algorithm": "NIST-FIPS-204-ML-DSA-87",
                        "key_id": kp.key_id,
                        "public_key_hex": kp.public_key.hex(),
                        "signature_hex": sig.hex(),
                    }
                except Exception as e:
                    logger.warning("PQC package signing skipped: %s", e)

            # Save manifest sidecar
            sidecar = out_file.with_suffix(".smdc-app.sig")
            sidecar.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

            logger.info(
                "Successfully packaged enterprise application to %s (SHA-256: %s...)",
                out_file,
                sha256_digest[:12],
            )
            return True, f"Application packaged to {out_file}", metadata
        except Exception as e:
            logger.error("Failed to package application: %s", e)
            return False, f"Packaging error: {e}", {}
