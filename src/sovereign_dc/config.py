"""Sovereign Mini Datacenter — Centralized Configuration Module.

Provides a single-source-of-truth configuration dataclass with layered loading:
1. Compiled defaults (production-ready values)
2. YAML configuration file overrides
3. Environment variable overrides (highest precedence)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Any

logger = logging.getLogger("SovereignConfig")


class HALMode(StrEnum):
    """Hardware abstraction layer operating mode."""

    SIMULATION = "simulation"
    HARDWARE = "hardware"


@dataclass
class SovereignConfig:
    """Unified configuration for all Sovereign Mini Datacenter subsystems.

    Values are resolved in precedence order:
      1. Environment variables (highest)
      2. YAML config file
      3. Dataclass defaults (lowest)
    """

    # ── Node Identity ─────────────────────────────────────────────────────
    node_id: str = "smdc-dgx-01"
    node_role: str = "Primary Compute Core"

    # ── Hardware Abstraction ──────────────────────────────────────────────
    hal_mode: str = "simulation"

    # ── Network Endpoints ─────────────────────────────────────────────────
    ollama_url: str = "http://localhost:11434"
    qdrant_url: str = "http://localhost:6333"
    prometheus_url: str = "http://prometheus:9090"
    power_exporter_url: str = "http://localhost:9101/metrics"
    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883

    # ── Space & DTN ───────────────────────────────────────────────────────
    ground_station_lat: float = 37.9838
    ground_station_lon: float = 23.7275
    ground_station_name: str = "Sovereign-Ground-01"
    dtn_db_path: str = ""
    technician_eid: str = "dtn://technician.sovereign.space/alerts"

    # ── Telemetry Exporter ────────────────────────────────────────────────
    exporter_port: int = 9101
    space_exporter_port: int = 9102

    # ── AI Models ─────────────────────────────────────────────────────────
    ollama_default_model: str = "qwen2.5-coder:7b"
    embedding_model: str = "nomic-embed-text"
    eco_embedding_model: str = "all-minilm"
    code_review_model: str = "qwen2.5-coder:7b"

    # ── Paths ─────────────────────────────────────────────────────────────
    docs_watch_dir: str = "/data/documents"
    log_dir: str = ""
    cluster_config_path: str = "cluster_config.yaml"

    # ── Load Shedding Thresholds ──────────────────────────────────────────
    shedding_l1_soc: float = 50.0
    shedding_l2_soc: float = 30.0
    shedding_l3_soc: float = 20.0
    shedding_l4_soc: float = 10.0
    sentinel_poll_interval: int = 30
    mesh_poll_interval: int = 60

    # ── Derived / Runtime ─────────────────────────────────────────────────
    _loaded_from: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        """Resolve platform-dependent defaults after initialization."""
        if not self.dtn_db_path:
            self.dtn_db_path = os.path.join(os.environ.get("TEMP", "/tmp"), "dtn_spool.db")
        if not self.log_dir:
            self.log_dir = os.path.join(os.environ.get("TEMP", "/tmp"), "sovereign_logs")

    @classmethod
    def from_env(cls) -> SovereignConfig:
        """Create configuration from environment variables only.

        Environment variable names are derived from field names by uppercasing.
        Example: ``node_id`` -> ``NODE_ID``, ``ollama_url`` -> ``OLLAMA_URL``.
        Legacy env var names (e.g. ``OLLAMA_BASE_URL``) are also supported.
        """
        env_map = _build_env_map()
        kwargs: dict[str, Any] = {}

        for f in fields(cls):
            if f.name.startswith("_"):
                continue
            env_key = f.name.upper()
            # Check canonical name first, then legacy aliases
            val = os.environ.get(env_key)
            if val is None and env_key in env_map:
                for alias in env_map[env_key]:
                    val = os.environ.get(alias)
                    if val is not None:
                        break
            if val is not None:
                kwargs[f.name] = _coerce(val, f.type)

        cfg = cls(**kwargs)
        cfg._loaded_from = ["env"]
        return cfg

    @classmethod
    def from_yaml_and_env(cls, yaml_path: str = "sovereign.yaml") -> SovereignConfig:
        """Load configuration from a YAML file with environment variable overrides.

        Args:
            yaml_path: Path to YAML configuration file. Missing files are silently ignored.

        Returns:
            Fully resolved configuration instance.
        """
        yaml_values: dict[str, Any] = {}
        sources: list[str] = []

        if os.path.isfile(yaml_path):
            try:
                import yaml

                with open(yaml_path, encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh)
                if isinstance(raw, dict):
                    yaml_values = raw
                    sources.append(f"yaml:{yaml_path}")
                    logger.info("Loaded configuration from %s", yaml_path)
            except ImportError:
                logger.debug("PyYAML not installed; skipping YAML config file.")
            except Exception as exc:
                logger.warning("Failed to load config from %s: %s", yaml_path, exc)

        # Merge: YAML values first, then env overrides on top
        env_map = _build_env_map()
        kwargs: dict[str, Any] = {}

        for f in fields(cls):
            if f.name.startswith("_"):
                continue
            # Layer 1: YAML value
            if f.name in yaml_values:
                kwargs[f.name] = _coerce(yaml_values[f.name], f.type)
            # Layer 2: Environment variable override
            env_key = f.name.upper()
            val = os.environ.get(env_key)
            if val is None and env_key in env_map:
                for alias in env_map[env_key]:
                    val = os.environ.get(alias)
                    if val is not None:
                        break
            if val is not None:
                kwargs[f.name] = _coerce(val, f.type)

        sources.append("env")
        cfg = cls(**kwargs)
        cfg._loaded_from = sources
        return cfg

    def is_simulation(self) -> bool:
        """Check if HAL is running in simulation mode."""
        return self.hal_mode == HALMode.SIMULATION

    def get_shedding_level(self, battery_soc: float) -> int:
        """Determine load shedding level from battery State-of-Charge.

        Returns:
            0 (L0 Nominal) through 4 (L4 Blackout Safe).
        """
        if battery_soc < self.shedding_l4_soc:
            return 4
        if battery_soc < self.shedding_l3_soc:
            return 3
        if battery_soc < self.shedding_l2_soc:
            return 2
        if battery_soc < self.shedding_l1_soc:
            return 1
        return 0


def _build_env_map() -> dict[str, list[str]]:
    """Map canonical env var names to legacy aliases for backward compatibility."""
    return {
        "OLLAMA_URL": ["OLLAMA_BASE_URL"],
        "QDRANT_URL": ["QDRANT_BASE_URL"],
        "POWER_EXPORTER_URL": ["POWER_EXPORTER_URL"],
        "EXPORTER_PORT": ["EXPORTER_PORT"],
        "SPACE_EXPORTER_PORT": ["SPACE_EXPORTER_PORT"],
        "DTN_DB_PATH": ["DTN_DB_PATH"],
        "LOG_DIR": ["SOVEREIGN_LOG_DIR"],
        "OLLAMA_DEFAULT_MODEL": ["OLLAMA_DEFAULT_MODEL"],
        "CODE_REVIEW_MODEL": ["OLLAMA_CODE_MODEL"],
        "NODE_ID": ["NODE_ID"],
        "NODE_ROLE": ["NODE_ROLE"],
    }


def _coerce(value: Any, type_hint: str | type) -> Any:
    """Coerce a string or raw value to the declared field type."""
    hint = str(type_hint)
    if isinstance(value, str):
        if hint in ("int", "<class 'int'>"):
            return int(value)
        if hint in ("float", "<class 'float'>"):
            return float(value)
        if hint in ("bool", "<class 'bool'>"):
            return value.lower() in ("true", "1", "yes")
    return value


# ── Module-level singleton ────────────────────────────────────────────────
_global_config: SovereignConfig | None = None


def get_config() -> SovereignConfig:
    """Return the global configuration singleton, creating it on first access."""
    global _global_config
    if _global_config is None:
        _global_config = SovereignConfig.from_env()
    return _global_config


def set_config(config: SovereignConfig) -> None:
    """Replace the global configuration singleton (useful for testing)."""
    global _global_config
    _global_config = config


def reset_config() -> None:
    """Clear the global configuration singleton."""
    global _global_config
    _global_config = None
