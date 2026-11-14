"""Sovereign Mini Datacenter — Centralized Configuration Module.

Provides a single-source-of-truth configuration dataclass with layered loading:
1. Compiled defaults (production-ready values)
2. YAML configuration file overrides
3. Environment variable overrides (highest precedence)
"""

from __future__ import annotations

import logging
import os
import stat
import tempfile
from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Any

logger = logging.getLogger("SovereignConfig")

# File names of node-local state inside the private state directory.
DTN_SPOOL_FILENAME = "dtn_spool.db"
LOG_DIRNAME = "sovereign_logs"
MODE_FILENAME = "sovereign_mode"
# Shared temp directory in which older releases kept their state files by default.
LEGACY_TMP_DIR = "/tmp"

_FALLBACK_STATE_DIR: str | None = None


def _is_private_dir(path: str) -> bool:
    """True if ``path`` is a real directory (not a symlink) owned by us with mode 0700."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700


def get_state_dir() -> str:
    """Returns the user-private (0700) directory that holds node-local state.

    ``SMDC_STATE_DIR`` if set, else ``~/.smdc``; created and chmod-ed to 0700. This is where
    the wallet, ledger, node identity key, DTN spool, technician alert log and sentinel mode
    file live by default. Shared ``/tmp`` is never a default: any local user can pre-create a
    predictable name there, and it does not survive a reboot.

    If that directory cannot be created, falls back to ``<tmp>/smdc-<uid>`` only when it is
    verifiably ours (owned by this user, mode 0700, not a symlink); a pre-existing directory
    that fails the check is never trusted and a fresh ``mkdtemp()`` directory is used for
    the rest of the process instead.
    """
    global _FALLBACK_STATE_DIR
    base_dir = os.environ.get("SMDC_STATE_DIR") or os.path.expanduser("~/.smdc")
    try:
        os.makedirs(base_dir, mode=0o700, exist_ok=True)
        os.chmod(base_dir, 0o700)
        return base_dir
    except OSError:
        pass

    if _FALLBACK_STATE_DIR is not None and _is_private_dir(_FALLBACK_STATE_DIR):
        return _FALLBACK_STATE_DIR

    fallback = os.path.join(tempfile.gettempdir(), f"smdc-{os.getuid()}")
    try:
        os.mkdir(fallback, 0o700)
    except OSError:
        pass  # Already exists (or cannot be created): decided by the check below.
    if not _is_private_dir(fallback):
        logger.warning("Not trusting pre-existing state directory %s; using a private temporary directory", fallback)
        fallback = tempfile.mkdtemp(prefix=f"smdc-{os.getuid()}-")
    _FALLBACK_STATE_DIR = fallback
    return fallback


def is_trusted_legacy_file(path: str) -> bool:
    """True if ``path`` (a file an older release kept in shared ``/tmp``) may still be used.

    It must be a regular file (not a symlink), owned by the current user and not writable
    by group or others. Anything else may have been planted by another local user.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and not (st.st_mode & 0o022)


def _legacy_tmp_paths(filename: str) -> list[str]:
    """Where releases that defaulted to shared temp storage kept ``filename``."""
    candidates = [
        os.path.join(os.environ.get("TEMP") or LEGACY_TMP_DIR, filename),
        os.path.join(LEGACY_TMP_DIR, filename),
    ]
    return list(dict.fromkeys(candidates))


def resolve_state_file(filename: str, sidecar_suffixes: tuple[str, ...] = ()) -> str:
    """Default path of a node-local state file: ``<state dir>/<filename>``.

    Legacy fallback: if that file does not exist yet but an older release left
    ``<tmp>/<filename>`` behind and it passes ``is_trusted_legacy_file`` (together with any
    existing ``sidecar_suffixes`` companions such as SQLite's ``-wal``), the old path is
    returned so that its contents are not orphaned. A file in ``/tmp`` that fails the check
    is ignored.
    """
    path = os.path.join(get_state_dir(), filename)
    if os.path.lexists(path):
        return path
    for legacy in _legacy_tmp_paths(filename):
        if not is_trusted_legacy_file(legacy):
            continue
        sidecars = [legacy + suffix for suffix in sidecar_suffixes]
        if all(is_trusted_legacy_file(s) for s in sidecars if os.path.lexists(s)):
            logger.warning("Using legacy state file %s; move it to %s to keep it across reboots", legacy, path)
            return legacy
    return path


def default_dtn_db_path() -> str:
    """Default DTN store-and-forward spool: ``<state dir>/dtn_spool.db`` (see ``resolve_state_file``)."""
    return resolve_state_file(DTN_SPOOL_FILENAME, sidecar_suffixes=("-wal", "-shm", "-journal"))


def default_log_dir() -> str:
    """Default directory of the technician alert log: ``<state dir>/sovereign_logs``."""
    return os.path.join(get_state_dir(), LOG_DIRNAME)


def default_mode_file() -> str:
    """Default sentinel mode file: ``<state dir>/sovereign_mode``.

    No legacy fallback: the mode is rewritten by the sentinel on its first evaluation, so
    nothing is lost by leaving an old ``/tmp/sovereign_mode`` behind.
    """
    return os.path.join(get_state_dir(), MODE_FILENAME)


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
        """Resolve defaults that depend on the node's private state directory.

        ``dtn_db_path`` and ``log_dir`` default to files under ``get_state_dir()``
        (``SMDC_STATE_DIR`` or ``~/.smdc``, mode 0700), not shared ``/tmp``. Explicit values
        (YAML, ``DTN_DB_PATH``, ``LOG_DIR`` / ``SOVEREIGN_LOG_DIR``) are used unchanged.
        """
        if not self.dtn_db_path:
            self.dtn_db_path = default_dtn_db_path()
        if not self.log_dir:
            self.log_dir = default_log_dir()

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
