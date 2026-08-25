"""Sovereign Mini Datacenter — Enterprise Application Schema & Manifest Specification.

Provides standardized dataclasses, enums, and validation logic for onboarding
any enterprise workload, IoT aggregator, AI model, or microservice onto SMDC.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class AppCategory(StrEnum):
    """Enterprise application operational category."""

    IOT = "iot"
    AI_INFERENCE = "ai_inference"
    SPATIAL_MEDIA = "spatial_media"
    DATABASE = "database"
    DISTRIBUTED = "distributed"
    WEB_SERVICE = "web_service"
    CUSTOM = "custom"


class PowerPriority(StrEnum):
    """Hardware-enforced power shedding tier (L0 to L4).

    - L0_CRITICAL: Never shed. Always active (telemetry, security, life support).
    - L1_STANDARD: Core operational services (active business APIs, interactive UI).
    - L2_BACKGROUND: Paused when battery SoC < 40% (continuous indexing, batch processing).
    - L3_DEFERRABLE: Paused when battery SoC < 60% or solar < 200W (heavy model retraining).
    - L4_IDLE: Active only during solar surplus (> 800W) or battery SoC > 80%.
    """

    L0_CRITICAL = "L0_CRITICAL"
    L1_STANDARD = "L1_STANDARD"
    L2_BACKGROUND = "L2_BACKGROUND"
    L3_DEFERRABLE = "L3_DEFERRABLE"
    L4_IDLE = "L4_IDLE"


class RuntimeType(StrEnum):
    """Execution runtime engine for enterprise workloads."""

    PROCESS = "process"
    DOCKER = "docker"
    SYSTEMD = "systemd"
    WASM = "wasm"


class AppStatus(StrEnum):
    """Lifecycle runtime state of an onboarded enterprise application."""

    REGISTERED = "registered"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    ERROR = "error"


@dataclass
class ResourceQuotas:
    """Hardware compute and storage quotas allocated to an enterprise app."""

    cpu_cores: float = 1.0
    ram_mb: int = 512
    gpu_vram_mb: int = 0
    storage_mb: int = 1024
    gpu_required: bool = False
    max_power_w: float = 25.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ResourceQuotas:
        if not data:
            return cls()
        return cls(
            cpu_cores=float(data.get("cpu_cores", 1.0)),
            ram_mb=int(data.get("ram_mb", 512)),
            gpu_vram_mb=int(data.get("gpu_vram_mb", 0)),
            storage_mb=int(data.get("storage_mb", 1024)),
            gpu_required=bool(data.get("gpu_required", False)),
            max_power_w=float(data.get("max_power_w", 25.0)),
        )


@dataclass
class PowerPolicy:
    """Solar and battery dynamic shedding rules."""

    tier: PowerPriority = PowerPriority.L1_STANDARD
    min_battery_soc: float = 30.0
    max_ambient_temp_c: float = 65.0
    allow_solar_burst: bool = True
    min_solar_watts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PowerPolicy:
        if not data:
            return cls()
        raw_tier = data.get("tier", PowerPriority.L1_STANDARD.value)
        try:
            tier = PowerPriority(raw_tier)
        except ValueError:
            tier = PowerPriority.L1_STANDARD
        return cls(
            tier=tier,
            min_battery_soc=float(data.get("min_battery_soc", 30.0)),
            max_ambient_temp_c=float(data.get("max_ambient_temp_c", 65.0)),
            allow_solar_burst=bool(data.get("allow_solar_burst", True)),
            min_solar_watts=float(data.get("min_solar_watts", 0.0)),
        )


@dataclass
class NetworkPolicy:
    """Multi-spectral communication and network routing bindings."""

    ports: list[int] = field(default_factory=list)
    expose_wireguard: bool = True
    space_dtn_enabled: bool = False
    lora_heartbeat: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> NetworkPolicy:
        if not data:
            return cls()
        return cls(
            ports=[int(p) for p in data.get("ports", [])],
            expose_wireguard=bool(data.get("expose_wireguard", True)),
            space_dtn_enabled=bool(data.get("space_dtn_enabled", False)),
            lora_heartbeat=bool(data.get("lora_heartbeat", False)),
        )


@dataclass
class StoragePolicy:
    """Persistence and volume bindings on encrypted NVMe."""

    persistent_volume: str = ""
    mount_point: str = ""
    backup_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> StoragePolicy:
        if not data:
            return cls()
        return cls(
            persistent_volume=str(data.get("persistent_volume", "")),
            mount_point=str(data.get("mount_point", "")),
            backup_enabled=bool(data.get("backup_enabled", True)),
        )


@dataclass
class HealthProbe:
    """Health check probe configuration."""

    type: str = "http"  # 'http', 'tcp', 'command'
    endpoint: str = "/health"
    port: int | None = None
    interval_sec: int = 15
    timeout_sec: int = 5
    max_retries: int = 3

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HealthProbe:
        if not data:
            return cls()
        port_val = data.get("port")
        port: int | None = None
        if port_val is not None and str(port_val).lower() not in ["none", "null", ""]:
            try:
                port = int(port_val)
            except (ValueError, TypeError):
                port = None
        return cls(
            type=str(data.get("type", "http")),
            endpoint=str(data.get("endpoint", "/health")),
            port=port,
            interval_sec=int(data.get("interval_sec", 15)),
            timeout_sec=int(data.get("timeout_sec", 5)),
            max_retries=int(data.get("max_retries", 3)),
        )


@dataclass
class AppManifest:
    """Standardized SMDC Enterprise Application Manifest (`smdc-app.yaml`)."""

    name: str
    app_id: str
    version: str = "1.0.0"
    description: str = ""
    author: str = "Enterprise Developer"
    category: AppCategory = AppCategory.CUSTOM
    runtime: RuntimeType = RuntimeType.PROCESS
    entrypoint: str = "python3 app.py"
    environment: dict[str, str] = field(default_factory=dict)
    resources: ResourceQuotas = field(default_factory=ResourceQuotas)
    power: PowerPolicy = field(default_factory=PowerPolicy)
    network: NetworkPolicy = field(default_factory=NetworkPolicy)
    storage: StoragePolicy = field(default_factory=StoragePolicy)
    health_check: HealthProbe = field(default_factory=HealthProbe)

    def validate(self) -> list[str]:
        """Validate manifest fields against security and runtime rules.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = []
        if not self.name or not self.name.strip():
            errors.append("App 'name' is required.")
        if not self.app_id or not re.match(r"^[a-z0-9][a-z0-9_-]{2,63}$", self.app_id):
            errors.append("App 'app_id' must be 3-64 characters, lowercase alphanumeric with hyphens/underscores.")
        if not self.entrypoint or not self.entrypoint.strip():
            errors.append("App 'entrypoint' command is required.")
        if self.resources.cpu_cores <= 0:
            errors.append("Resource 'cpu_cores' must be greater than 0.")
        if self.resources.ram_mb < 32:
            errors.append("Resource 'ram_mb' must be at least 32 MB.")
        if self.power.min_battery_soc < 0 or self.power.min_battery_soc > 100:
            errors.append("Power 'min_battery_soc' must be between 0 and 100.")
        for port in self.network.ports:
            if port < 1 or port > 65535:
                errors.append(f"Network port {port} is out of valid range (1-65535).")
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to serializable dictionary."""
        return {
            "name": self.name,
            "app_id": self.app_id,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "category": self.category.value,
            "runtime": self.runtime.value,
            "entrypoint": self.entrypoint,
            "environment": dict(self.environment),
            "resources": self.resources.to_dict(),
            "power": self.power.to_dict(),
            "network": self.network.to_dict(),
            "storage": self.storage.to_dict(),
            "health_check": self.health_check.to_dict(),
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize manifest to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppManifest:
        """Construct AppManifest from parsed dictionary."""
        raw_cat = data.get("category", AppCategory.CUSTOM.value)
        try:
            category = AppCategory(raw_cat)
        except ValueError:
            category = AppCategory.CUSTOM

        raw_runtime = data.get("runtime", RuntimeType.PROCESS.value)
        try:
            runtime = RuntimeType(raw_runtime)
        except ValueError:
            runtime = RuntimeType.PROCESS

        app_id = data.get("app_id") or data.get("id") or ""
        name = data.get("name", "")

        return cls(
            name=name,
            app_id=app_id,
            version=str(data.get("version", "1.0.0")),
            description=str(data.get("description", "")),
            author=str(data.get("author", "Enterprise Developer")),
            category=category,
            runtime=runtime,
            entrypoint=str(data.get("entrypoint", "")),
            environment=dict(data.get("environment", {})),
            resources=ResourceQuotas.from_dict(data.get("resources")),
            power=PowerPolicy.from_dict(data.get("power")),
            network=NetworkPolicy.from_dict(data.get("network")),
            storage=StoragePolicy.from_dict(data.get("storage")),
            health_check=HealthProbe.from_dict(data.get("health_check")),
        )

    @classmethod
    def from_json(cls, json_str: str) -> AppManifest:
        """Parse AppManifest from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)
