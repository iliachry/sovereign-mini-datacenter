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


def _is_strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _to_public_dict(instance: Any) -> dict[str, Any]:
    data = asdict(instance)
    data.pop("_parse_errors", None)
    return data


def _float_field(data: dict[str, Any], key: str, default: float, section: str, errors: list[str]) -> float:
    if key not in data:
        return default
    val = data[key]
    if _is_finite_number(val):
        return float(val)
    errors.append(f"{section} '{key}' must be a finite number.")
    return default


def _int_field(data: dict[str, Any], key: str, default: int, section: str, errors: list[str]) -> int:
    if key not in data:
        return default
    val = data[key]
    if _is_strict_int(val):
        return val
    errors.append(f"{section} '{key}' must be an integer.")
    return default


def _bool_field(data: dict[str, Any], key: str, default: bool, section: str, errors: list[str]) -> bool:
    if key not in data:
        return default
    val = data[key]
    if isinstance(val, bool):
        return val
    errors.append(f"{section} '{key}' must be a boolean.")
    return default


def _str_field(
    data: dict[str, Any],
    key: str,
    default: str,
    section: str,
    errors: list[str],
    *,
    non_empty: bool = False,
) -> str:
    if key not in data:
        return default
    val = data[key]
    if isinstance(val, str) and (not non_empty or bool(val.strip())):
        return val
    kind = "non-empty string" if non_empty else "string"
    errors.append(f"{section} '{key}' must be a {kind}.")
    return default


def _sub_section(policy_cls: Any, data: dict[str, Any], key: str, errors: list[str]) -> Any:
    if key not in data:
        return policy_cls()
    raw = data[key]
    if not isinstance(raw, dict):
        errors.append(f"Section '{key}' must be a mapping.")
        return policy_cls()
    return policy_cls.from_dict(raw)


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
    _parse_errors: list[str] = field(default_factory=list, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return _to_public_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ResourceQuotas:
        if data is None:
            return cls()
        if not isinstance(data, dict):
            return cls(_parse_errors=["Section 'resources' must be a mapping."])
        if not data:
            return cls()
        parse_errors: list[str] = []
        return cls(
            cpu_cores=_float_field(data, "cpu_cores", 1.0, "Resource", parse_errors),
            ram_mb=_int_field(data, "ram_mb", 512, "Resource", parse_errors),
            gpu_vram_mb=_int_field(data, "gpu_vram_mb", 0, "Resource", parse_errors),
            storage_mb=_int_field(data, "storage_mb", 1024, "Resource", parse_errors),
            gpu_required=_bool_field(data, "gpu_required", False, "Resource", parse_errors),
            max_power_w=_float_field(data, "max_power_w", 25.0, "Resource", parse_errors),
            _parse_errors=parse_errors,
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
    _parse_errors: list[str] = field(default_factory=list, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        d = _to_public_dict(self)
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
        if data is None:
            return cls()
        if not isinstance(data, dict):
            return cls(_parse_errors=["Section 'power' must be a mapping."])
        if not data:
            return cls()

        parse_errors: list[str] = []
        tier = PowerPriority.L1_STANDARD
        if "tier" in data:
            raw_tier = data["tier"]
            if isinstance(raw_tier, str):
                try:
                    tier = PowerPriority(raw_tier)
                except ValueError:
                    parse_errors.append(f"Power 'tier' {raw_tier!r} is invalid.")
            else:
                parse_errors.append("Power 'tier' must be a valid priority tier string.")

        return cls(
            tier=tier,
            min_battery_soc=_float_field(data, "min_battery_soc", 30.0, "Power", parse_errors),
            max_ambient_temp_c=_float_field(data, "max_ambient_temp_c", 65.0, "Power", parse_errors),
            allow_solar_burst=_bool_field(data, "allow_solar_burst", True, "Power", parse_errors),
            min_solar_watts=_float_field(data, "min_solar_watts", 0.0, "Power", parse_errors),
            _parse_errors=parse_errors,
        )


@dataclass
class NetworkPolicy:
    """Multi-spectral communication and network routing bindings."""

    ports: list[int] = field(default_factory=list)
    expose_wireguard: bool = True
    space_dtn_enabled: bool = False
    lora_heartbeat: bool = False
    _parse_errors: list[str] = field(default_factory=list, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return _to_public_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> NetworkPolicy:
        if data is None:
            return cls()
        if not isinstance(data, dict):
            return cls(_parse_errors=["Section 'network' must be a mapping."])
        if not data:
            return cls()

        parse_errors: list[str] = []
        ports: list[int] = []
        if "ports" in data:
            raw_ports = data["ports"]
            if not isinstance(raw_ports, list):
                parse_errors.append("Network 'ports' must be a list of integers.")
            else:
                for p in raw_ports:
                    if _is_strict_int(p):
                        ports.append(p)
                    else:
                        parse_errors.append(f"Network port {p!r} must be an integer between 1 and 65535.")

        return cls(
            ports=ports,
            expose_wireguard=_bool_field(data, "expose_wireguard", True, "Network", parse_errors),
            space_dtn_enabled=_bool_field(data, "space_dtn_enabled", False, "Network", parse_errors),
            lora_heartbeat=_bool_field(data, "lora_heartbeat", False, "Network", parse_errors),
            _parse_errors=parse_errors,
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
    _parse_errors: list[str] = field(default_factory=list, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return _to_public_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> StoragePolicy:
        if data is None:
            return cls()
        if not isinstance(data, dict):
            return cls(_parse_errors=["Section 'storage' must be a mapping."])
        if not data:
            return cls()

        parse_errors: list[str] = []
        return cls(
            persistent_volume=_str_field(data, "persistent_volume", "", "Storage", parse_errors),
            mount_point=_str_field(data, "mount_point", "", "Storage", parse_errors),
            backup_enabled=_bool_field(data, "backup_enabled", True, "Storage", parse_errors),
            _parse_errors=parse_errors,
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
    _parse_errors: list[str] = field(default_factory=list, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return _to_public_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HealthProbe:
        if data is None:
            return cls()
        if not isinstance(data, dict):
            return cls(_parse_errors=["Section 'health_check' must be a mapping."])
        if not data:
            return cls()

        parse_errors: list[str] = []
        port: int | None = None
        if "port" in data and data["port"] is not None:
            raw_port = data["port"]
            if _is_strict_int(raw_port):
                port = raw_port
            else:
                parse_errors.append("Health check 'port' must be an integer between 1 and 65535.")

        return cls(
            type=_str_field(data, "type", "http", "Health check", parse_errors, non_empty=True),
            endpoint=_str_field(data, "endpoint", "/health", "Health check", parse_errors),
            port=port,
            interval_sec=_int_field(data, "interval_sec", 15, "Health check", parse_errors),
            timeout_sec=_int_field(data, "timeout_sec", 5, "Health check", parse_errors),
            max_retries=_int_field(data, "max_retries", 3, "Health check", parse_errors),
            _parse_errors=parse_errors,
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
    _parse_errors: list[str] = field(default_factory=list, repr=False, compare=False)

    def parse_errors(self) -> list[str]:
        """Return type/coercion/enum errors recorded during ``from_dict`` parsing."""
        return [
            *self._parse_errors,
            *self.resources._parse_errors,
            *self.power._parse_errors,
            *self.network._parse_errors,
            *self.storage._parse_errors,
            *self.health_check._parse_errors,
        ]

    def validate(self) -> list[str]:
        """Validate manifest fields against security and runtime rules.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = self.parse_errors()

        if not isinstance(self.name, str) or not self.name.strip():
            errors.append("App 'name' is required.")
        if not is_valid_app_id(self.app_id):
            errors.append("App 'app_id' must be 3-64 characters, lowercase alphanumeric with hyphens/underscores.")
        if not is_valid_app_version(self.version):
            errors.append(
                "App 'version' must be 1-64 characters of letters, digits, '.', '_', '+' or '-' "
                "and start with a letter or digit."
            )
        if not isinstance(self.description, str):
            errors.append("App 'description' must be a string.")
        if not isinstance(self.author, str) or not self.author.strip():
            errors.append("App 'author' must be a non-empty string.")
        if not isinstance(self.category, AppCategory):
            errors.append("App 'category' must be a valid AppCategory.")
        if not isinstance(self.runtime, RuntimeType):
            errors.append("App 'runtime' must be a valid RuntimeType.")
        if not isinstance(self.entrypoint, str) or not self.entrypoint.strip():
            errors.append("App 'entrypoint' command is required.")
        if not isinstance(self.environment, dict):
            errors.append("App 'environment' must be a mapping.")
        else:
            for env_k, env_v in self.environment.items():
                if not isinstance(env_k, str) or not env_k.strip() or not isinstance(env_v, str):
                    errors.append("App 'environment' must map non-empty string keys to string values.")

        if not _is_finite_number(self.resources.cpu_cores) or self.resources.cpu_cores <= 0:
            errors.append("Resource 'cpu_cores' must be a finite number greater than 0.")
        if not _is_strict_int(self.resources.ram_mb) or self.resources.ram_mb < 32:
            errors.append("Resource 'ram_mb' must be an integer of at least 32 MB.")
        for int_quota in ("gpu_vram_mb", "storage_mb"):
            int_val = getattr(self.resources, int_quota)
            if not _is_strict_int(int_val) or int_val < 0:
                errors.append(f"Resource '{int_quota}' must be a non-negative integer.")
        if not _is_finite_number(self.resources.max_power_w) or self.resources.max_power_w < 0:
            errors.append("Resource 'max_power_w' must be a finite, non-negative number.")
        if not isinstance(self.resources.gpu_required, bool):
            errors.append("Resource 'gpu_required' must be a boolean.")

        if not isinstance(self.power.tier, PowerPriority):
            errors.append("Power 'tier' must be a valid PowerPriority.")
        soc = self.power.min_battery_soc
        if not _is_finite_number(soc) or soc < 0 or soc > 100:
            errors.append("Power 'min_battery_soc' must be between 0 and 100.")
        if not _is_finite_number(self.power.min_solar_watts) or self.power.min_solar_watts < 0:
            errors.append("Power 'min_solar_watts' must be a finite, non-negative number.")
        if not _is_finite_number(self.power.max_ambient_temp_c):
            errors.append("Power 'max_ambient_temp_c' must be a finite number.")
        if not isinstance(self.power.allow_solar_burst, bool):
            errors.append("Power 'allow_solar_burst' must be a boolean.")

        if not isinstance(self.network.ports, list):
            errors.append("Network 'ports' must be a list of integers.")
        else:
            for port in self.network.ports:
                if not _is_strict_int(port) or port < 1 or port > 65535:
                    errors.append(f"Network port {port} is out of valid range (1-65535).")
        for net_flag in ("expose_wireguard", "space_dtn_enabled", "lora_heartbeat"):
            if not isinstance(getattr(self.network, net_flag), bool):
                errors.append(f"Network '{net_flag}' must be a boolean.")

        if not isinstance(self.storage.persistent_volume, str):
            errors.append("Storage 'persistent_volume' must be a string.")
        if not isinstance(self.storage.mount_point, str):
            errors.append("Storage 'mount_point' must be a string.")
        if not isinstance(self.storage.backup_enabled, bool):
            errors.append("Storage 'backup_enabled' must be a boolean.")

        if not isinstance(self.health_check.type, str) or not self.health_check.type.strip():
            errors.append("Health check 'type' must be a non-empty string.")
        if not isinstance(self.health_check.endpoint, str):
            errors.append("Health check 'endpoint' must be a string.")
        if self.health_check.port is not None and (
            not _is_strict_int(self.health_check.port) or self.health_check.port < 1 or self.health_check.port > 65535
        ):
            errors.append(f"Health check port {self.health_check.port} is out of valid range (1-65535).")
        if not _is_strict_int(self.health_check.interval_sec) or self.health_check.interval_sec < 1:
            errors.append("Health check 'interval_sec' must be an integer >= 1.")
        if not _is_strict_int(self.health_check.timeout_sec) or self.health_check.timeout_sec < 1:
            errors.append("Health check 'timeout_sec' must be an integer >= 1.")
        if not _is_strict_int(self.health_check.max_retries) or self.health_check.max_retries < 0:
            errors.append("Health check 'max_retries' must be a non-negative integer.")
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
        if not isinstance(data, dict):
            return cls(
                name="",
                app_id="",
                entrypoint="",
                _parse_errors=["Manifest root must be a mapping."],
            )

        parse_errors: list[str] = []

        category = AppCategory.CUSTOM
        if "category" in data:
            raw_cat = data["category"]
            if isinstance(raw_cat, str):
                try:
                    category = AppCategory(raw_cat)
                except ValueError:
                    parse_errors.append(f"App 'category' {raw_cat!r} is invalid.")
            else:
                parse_errors.append("App 'category' must be a valid category string.")

        runtime = RuntimeType.PROCESS
        if "runtime" in data:
            raw_runtime = data["runtime"]
            if isinstance(raw_runtime, str):
                try:
                    runtime = RuntimeType(raw_runtime)
                except ValueError:
                    parse_errors.append(f"App 'runtime' {raw_runtime!r} is invalid.")
            else:
                parse_errors.append("App 'runtime' must be a valid runtime string.")

        app_id = data["app_id"] if "app_id" in data else data.get("id", "")
        name = data.get("name", "")

        if "version" not in data:
            version: Any = "1.0.0"
        else:
            raw_ver = data["version"]
            version = str(raw_ver) if _is_finite_number(raw_ver) else raw_ver

        description = "" if "description" not in data else data["description"]
        author = "Enterprise Developer" if "author" not in data else data["author"]
        entrypoint = "" if "entrypoint" not in data else data["entrypoint"]

        environment: dict[str, str] = {}
        if "environment" in data:
            raw_env = data["environment"]
            if isinstance(raw_env, dict):
                for env_k, env_v in raw_env.items():
                    if isinstance(env_k, str) and env_k.strip() and isinstance(env_v, str):
                        environment[env_k] = env_v
                    else:
                        parse_errors.append("App 'environment' must map non-empty string keys to string values.")
            else:
                parse_errors.append("App 'environment' must be a mapping.")

        return cls(
            name=name,
            app_id=app_id,
            version=version,
            description=description,
            author=author,
            category=category,
            runtime=runtime,
            entrypoint=entrypoint,
            environment=environment,
            resources=_sub_section(ResourceQuotas, data, "resources", parse_errors),
            power=_sub_section(PowerPolicy, data, "power", parse_errors),
            network=_sub_section(NetworkPolicy, data, "network", parse_errors),
            storage=_sub_section(StoragePolicy, data, "storage", parse_errors),
            health_check=_sub_section(HealthProbe, data, "health_check", parse_errors),
            _parse_errors=parse_errors,
        )

    @classmethod
    def from_json(cls, json_str: str) -> AppManifest:
        """Parse AppManifest from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)
