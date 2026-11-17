"""Sovereign Mini Datacenter — Enterprise Application Schema & Manifest Specification.

Provides standardized dataclasses, enums, and validation logic for onboarding
any enterprise workload, IoT aggregator, AI model, or microservice onto SMDC.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

APP_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{2,63}")
# Versions end up in file names (``<app_id>-<version>.smdc-app``): no separators, no leading dot.
APP_VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}")


def is_valid_app_id(app_id: object) -> bool:
    """True if ``app_id`` is a string of 3-64 lowercase alphanumerics, hyphens or underscores."""
    return isinstance(app_id, str) and APP_ID_PATTERN.fullmatch(app_id) is not None


def is_valid_app_version(version: object) -> bool:
    """True if ``version`` is a 1-64 character token that is safe to embed in a file name."""
    return isinstance(version, str) and APP_VERSION_PATTERN.fullmatch(version) is not None


def slugify_app_id(name: str) -> str:
    """Derives a manifest-valid ``app_id`` from a free-form display name.

    Lowercases, collapses every run of characters outside ``[a-z0-9]`` into a single hyphen
    and pads/truncates to the 3-64 character range, e.g. ``"My App! v2"`` -> ``"my-app-v2"``.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:64].rstrip("-")
    if len(slug) < 3:
        slug = f"app-{slug}" if slug else "app"
    return slug


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


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
    """Power shedding tier of an application (L0 to L4).

    The tier decides at which load-shedding level the ``EnterpriseManager`` marks the
    tracked application PAUSED (``SHED_PAUSE_LEVEL``). The level is
    derived from battery SoC with ``SovereignConfig.shedding_l1..l4_soc`` (defaults below).
    Pausing is a tracked state change plus an event: nothing is throttled, scheduled,
    terminated or enforced in hardware.

    - L0_CRITICAL: never paused by shedding (telemetry, security, life support).
    - L1_STANDARD: paused at level 4 (SoC < 10 %).
    - L2_BACKGROUND: paused at level 3 and above (SoC < 20 %).
    - L3_DEFERRABLE: paused at level 2 and above (SoC < 30 %).
    - L4_IDLE: paused at level 1 and above (SoC < 50 %).
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


# Lowest load-shedding level at which a tracked app of each power tier is marked PAUSED by
# the EnterpriseManager. L0_CRITICAL has no entry: it is never paused by shedding.
SHED_PAUSE_LEVEL: dict[PowerPriority, int] = {
    PowerPriority.L4_IDLE: 1,
    PowerPriority.L3_DEFERRABLE: 2,
    PowerPriority.L2_BACKGROUND: 3,
    PowerPriority.L1_STANDARD: 4,
}
POWER_POLICY_RESERVED_FIELDS = ("max_ambient_temp_c", "allow_solar_burst", "min_solar_watts")
POWER_POLICY_RESERVED_NOTICE = (
    "power.max_ambient_temp_c, power.allow_solar_burst and power.min_solar_watts are reserved, "
    "not enforced: they are validated and stored, but no component pauses or resumes the "
    "application because of them."
)


@dataclass
class PowerPolicy:
    """Load-shedding policy of one application.

    Enforced by ``EnterpriseManager`` on ``load_shedding.*`` events (tracked state only):

    - ``tier``: the shedding level from which the app is marked PAUSED (see ``PowerPriority``).
    - ``min_battery_soc``: when the event carries ``battery_soc``, the app is paused while
      that SoC is below this value and resumed at or above it plus 2 points of hysteresis (capped at 100 %).
      Ignored for ``L0_CRITICAL``.

    Reserved, not enforced (validated and stored only; see ``POWER_POLICY_RESERVED_NOTICE``):
    ``max_ambient_temp_c``, ``allow_solar_burst`` and ``min_solar_watts``.
    """

    tier: PowerPriority = PowerPriority.L1_STANDARD
    min_battery_soc: float = 30.0
    max_ambient_temp_c: float = 65.0
    allow_solar_burst: bool = True
    min_solar_watts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d

    def enforcement_summary(self) -> list[str]:
        """Plain-text lines saying what is enforced for this policy and what is not."""
        if self.tier == PowerPriority.L0_CRITICAL:
            lines = ["L0_CRITICAL is never paused by load shedding; min_battery_soc is ignored for it."]
        else:
            lines = [
                f"Paused (tracked state) from load-shedding level {SHED_PAUSE_LEVEL[self.tier]}, "
                f"or while a reported battery SoC is below min_battery_soc ({self.min_battery_soc:g} %)."
            ]
        lines.append(
            f"Reserved, not enforced: max_ambient_temp_c ({self.max_ambient_temp_c:g}), "
            f"allow_solar_burst ({str(self.allow_solar_burst).lower()}), "
            f"min_solar_watts ({self.min_solar_watts:g})."
        )
        return lines

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


BACKUP_DECLARATION_NOTICE = (
    "storage.backup_enabled is a declaration of intent only: no backup job reads it. "
    "To back the volume up, an operator must add it to the backup set by hand: one "
    "'key|name|files|.|reason' line in software/backup/volumes.conf and a read-only mount at "
    "/data/<key> in software/backup/docker-compose.backup.yml (see software/backup/README.md). "
    "The default Restic repository is local to the node, not off-site."
)


@dataclass
class StoragePolicy:
    """Persistence and volume bindings declared by the application.

    ``backup_enabled`` is a declaration of intent only: nothing in this package or in
    ``software/backup/`` reads it, and the application's volume is not part of the Restic
    backup set until an operator adds it there by hand (``software/backup/volumes.conf``;
    see ``BACKUP_DECLARATION_NOTICE``).
    """

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
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append("App 'name' is required.")
        if not is_valid_app_id(self.app_id):
            errors.append("App 'app_id' must be 3-64 characters, lowercase alphanumeric with hyphens/underscores.")
        if not is_valid_app_version(self.version):
            errors.append(
                "App 'version' must be 1-64 characters of letters, digits, '.', '_', '+' or '-' "
                "and start with a letter or digit."
            )
        if not isinstance(self.entrypoint, str) or not self.entrypoint.strip():
            errors.append("App 'entrypoint' command is required.")
        if not _is_finite_number(self.resources.cpu_cores) or self.resources.cpu_cores <= 0:
            errors.append("Resource 'cpu_cores' must be a finite number greater than 0.")
        if not _is_finite_number(self.resources.ram_mb) or self.resources.ram_mb < 32:
            errors.append("Resource 'ram_mb' must be at least 32 MB.")
        for quota in ("gpu_vram_mb", "storage_mb", "max_power_w"):
            value = getattr(self.resources, quota)
            if not _is_finite_number(value) or value < 0:
                errors.append(f"Resource '{quota}' must be a finite, non-negative number.")
        soc = self.power.min_battery_soc
        if not _is_finite_number(soc) or soc < 0 or soc > 100:
            errors.append("Power 'min_battery_soc' must be between 0 and 100.")
        if not _is_finite_number(self.power.min_solar_watts) or self.power.min_solar_watts < 0:
            errors.append("Power 'min_solar_watts' must be a finite, non-negative number.")
        if not _is_finite_number(self.power.max_ambient_temp_c):
            errors.append("Power 'max_ambient_temp_c' must be a finite number.")
        for port in self.network.ports:
            if not isinstance(port, int) or isinstance(port, bool) or port < 1 or port > 65535:
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
