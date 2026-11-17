"""Sovereign Mini Datacenter — Centralized Configuration Module.

Provides a single-source-of-truth configuration dataclass with layered loading:
1. Compiled defaults (production-ready values)
2. YAML configuration file overrides
3. Environment variable overrides (highest precedence)
"""

from __future__ import annotations

import logging
import math
import os
import stat
import tempfile
from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Any

logger = logging.getLogger("SovereignConfig")

# Node id used when neither NODE_ID nor a config file sets one. The mesh monitor
# (mesh/mesh_sync.py, which also runs standalone) carries the same literal.
DEFAULT_NODE_ID = "smdc-dgx-01"

# File names of node-local state inside the private state directory.
DTN_SPOOL_FILENAME = "dtn_spool.db"
LOG_DIRNAME = "sovereign_logs"
MODE_FILENAME = "sovereign_mode"
# Shared temp directory in which older releases kept their state files by default.
LEGACY_TMP_DIR = "/tmp"  # noqa: S108 - only read (after an ownership/mode check) to find a legacy file; never a default

_FALLBACK_STATE_DIR: str | None = None


# --- shared shedding policy: begin -----------------------------------------------------
# This block is byte-identical in src/sovereign_dc/config.py and
# src/sovereign_dc/agents/sentinel_copilot.py (the sentinel also ships as a standalone,
# standard-library-only script and cannot import the package); tests/ compares the two.
# It needs only ``math`` and the module's ``logger``.

# --- shedding thresholds (one rule): begin ----------------------------------------------
# This sub-block is byte-identical in src/sovereign_dc/config.py, the sentinel
# (src/sovereign_dc/agents/sentinel_copilot.py and its copy under software/agents/) and the
# power exporter (src/sovereign_dc/telemetry.py, software/telemetry/power_exporter.py and the
# Helm chart's copy); tests/ compares them. The standalone scripts are standard-library-only
# and cannot import the package. It needs only ``math`` and the module's ``logger``.
SHEDDING_ENV_NAMES = ("SHEDDING_L1_SOC", "SHEDDING_L2_SOC", "SHEDDING_L3_SOC", "SHEDDING_L4_SOC")
DEFAULT_SHEDDING_THRESHOLDS = (50.0, 30.0, 20.0, 10.0)


def shedding_policy_problem(thresholds: tuple[float, ...]) -> str | None:
    """Why ``thresholds`` (levels 1-4) are not a usable policy, or None when they are.

    Usable means: four finite numbers within 0-100 with L1 > L2 > L3 > L4.
    """
    if len(thresholds) != 4:
        return "four thresholds are needed"
    for name, value in zip(SHEDDING_ENV_NAMES, thresholds, strict=True):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return f"{name} is not a finite number"
        if not 0.0 <= value <= 100.0:
            return f"{name}={value:g} is outside 0-100"
    for index in range(3):
        if not thresholds[index] > thresholds[index + 1]:
            return (
                f"{SHEDDING_ENV_NAMES[index]}={thresholds[index]:g} is not above "
                f"{SHEDDING_ENV_NAMES[index + 1]}={thresholds[index + 1]:g}"
            )
    return None


def checked_shedding_thresholds(settings: tuple[object, ...]) -> tuple[float, float, float, float]:
    """The node's four SoC thresholds from the raw settings of levels 1-4. The one rule.

    ``settings`` holds, per level, what was configured: an environment string, a YAML value
    or a number; None or a blank string means "not set" and stands for that level's default.
    If ANY of the four is not a number, lies outside 0-100, or the order is not
    SHEDDING_L1_SOC > SHEDDING_L2_SOC > SHEDDING_L3_SOC > SHEDDING_L4_SOC, ALL four defaults
    are used and one warning names the four variables: a partly applied policy would be
    worse than the default one, and every reader of the policy must reach the same four
    numbers from the same settings.
    """
    values: list[float] = []
    for setting, default in zip(settings, DEFAULT_SHEDDING_THRESHOLDS, strict=False):
        if setting is None or (isinstance(setting, str) and not setting.strip()):
            values.append(default)
            continue
        try:
            values.append(math.nan if isinstance(setting, bool) else float(setting))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            values.append(math.nan)
    problem = shedding_policy_problem(tuple(values)) if len(settings) == 4 else "four thresholds are needed"
    if problem is None:
        return (values[0], values[1], values[2], values[3])
    logger.warning(
        "Ignoring the load-shedding thresholds %s (%s): they must be numbers within 0-100 with "
        "%s; using the defaults %s for all four.",
        ", ".join(f"{name}={setting!r}" for name, setting in zip(SHEDDING_ENV_NAMES, settings, strict=False)),
        problem,
        " > ".join(SHEDDING_ENV_NAMES),
        " / ".join(f"{value:g}" for value in DEFAULT_SHEDDING_THRESHOLDS),
    )
    return DEFAULT_SHEDDING_THRESHOLDS


# --- shedding thresholds (one rule): end ------------------------------------------------
SHEDDING_HYSTERESIS_ENV = "SHEDDING_HYSTERESIS_SOC"
DEFAULT_SHEDDING_HYSTERESIS_SOC = 2.0
MAX_SHEDDING_HYSTERESIS_SOC = 10.0


def checked_shedding_hysteresis(value: object) -> float:
    """``value`` as SoC points of hysteresis (0 .. MAX_SHEDDING_HYSTERESIS_SOC), else the default."""
    try:
        points = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        points = math.nan
    if isinstance(value, bool) or not math.isfinite(points) or not 0.0 <= points <= MAX_SHEDDING_HYSTERESIS_SOC:
        logger.warning(
            "Ignoring %s=%r (need a number from 0 to %g); using %g.",
            SHEDDING_HYSTERESIS_ENV,
            value,
            MAX_SHEDDING_HYSTERESIS_SOC,
            DEFAULT_SHEDDING_HYSTERESIS_SOC,
        )
        return DEFAULT_SHEDDING_HYSTERESIS_SOC
    return points


def compute_shedding_level(
    soc: float,
    thresholds: tuple[float, float, float, float],
    previous_level: int | None = None,
    hysteresis: float = 0.0,
) -> int:
    """Load-shedding level 0-4 for a battery SoC (percent). Pure.

    Without ``previous_level`` this is the plain mapping: level N while SoC is below the
    level-N threshold (the boundary itself belongs to the lower level).

    With ``previous_level`` (the level decided on the previous evaluation) the level rises
    exactly as in the plain mapping, but is left only once SoC is ``hysteresis`` points
    above that level's threshold: with thresholds 50 / 30 / 20 / 10 and hysteresis 2, a
    node at level 2 stays there until SoC reaches 32, so a SoC hovering around 30 does
    not toggle the level (and with it applications) on every poll.
    """
    level = 0
    for index, threshold in enumerate(thresholds):
        if soc < threshold:
            level = index + 1
    if previous_level is None or hysteresis <= 0.0 or previous_level <= level:
        return level
    held = min(int(previous_level), len(thresholds))
    while held > level and soc >= thresholds[held - 1] + hysteresis:
        held -= 1
    return held


# --- shared shedding policy: end -------------------------------------------------------

# SovereignConfig fields of the four thresholds, in level order (shedding_l1_soc .. l4).
_SHEDDING_THRESHOLD_FIELDS = tuple(name.lower() for name in SHEDDING_ENV_NAMES)


def _current_uid() -> int | None:
    """Current POSIX user ID, or ``None`` on platforms without ``os.getuid`` (Windows)."""
    getuid = getattr(os, "getuid", None)
    return int(getuid()) if callable(getuid) else None


def _is_private_dir(path: str) -> bool:
    """True if ``path`` is a real directory (not a symlink) owned by us with mode 0700."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    uid = _current_uid()
    if uid is None:
        return True
    return st.st_uid == uid and stat.S_IMODE(st.st_mode) == 0o700


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

    uid = _current_uid()
    uid_tag = str(uid) if uid is not None else "local"
    fallback = os.path.join(tempfile.gettempdir(), f"smdc-{uid_tag}")
    try:
        os.mkdir(fallback, 0o700)
    except OSError:
        pass  # Already exists (or cannot be created): decided by the check below.
    if not _is_private_dir(fallback):
        logger.warning("Not trusting pre-existing state directory %s; using a private temporary directory", fallback)
        fallback = tempfile.mkdtemp(prefix=f"smdc-{uid_tag}-")
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
    if not stat.S_ISREG(st.st_mode):
        return False
    uid = _current_uid()
    if uid is None:
        return True
    return st.st_uid == uid and not (st.st_mode & 0o022)


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
    node_id: str = DEFAULT_NODE_ID
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
    # Read from DOCS_WATCH_DIR, the variable the standalone knowledge indexer reads; it is
    # the default directory of `smdc agent index`.
    docs_watch_dir: str = "/data/documents"
    log_dir: str = ""
    cluster_config_path: str = "cluster_config.yaml"

    # ── Load Shedding Thresholds ──────────────────────────────────────────
    shedding_l1_soc: float = 50.0
    shedding_l2_soc: float = 30.0
    shedding_l3_soc: float = 20.0
    shedding_l4_soc: float = 10.0
    # SoC points a level's threshold must be exceeded by before that level is left again
    # (SHEDDING_HYSTERESIS_SOC; used only by callers that pass ``previous_level``).
    shedding_hysteresis_soc: float = DEFAULT_SHEDDING_HYSTERESIS_SOC
    # Seconds between sentinel telemetry polls. Read from SENTINEL_POLL_INTERVAL_SECONDS, the
    # variable the standalone sentinel agent reads (SENTINEL_POLL_INTERVAL also works here).
    sentinel_poll_interval: float = 30.0
    # Seconds between mesh peer health sweeps. Read from MESH_POLL_INTERVAL_SECONDS, the
    # variable the mesh sync daemon reads (MESH_POLL_INTERVAL also works here); at least 1.
    mesh_poll_interval: float = 60.0

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
        try:
            mesh_interval = float(self.mesh_poll_interval)
        except (TypeError, ValueError):
            mesh_interval = math.nan
        self.mesh_poll_interval = max(1.0, mesh_interval) if math.isfinite(mesh_interval) else 60.0
        # One SoC policy: four numbers within 0-100 with L1 > L2 > L3 > L4; otherwise all four
        # defaults. The loaders pass the raw settings of these four fields through unchanged
        # (see _assign_coerced) so that an unparsable one is judged here, with the others.
        (
            self.shedding_l1_soc,
            self.shedding_l2_soc,
            self.shedding_l3_soc,
            self.shedding_l4_soc,
        ) = checked_shedding_thresholds(
            (self.shedding_l1_soc, self.shedding_l2_soc, self.shedding_l3_soc, self.shedding_l4_soc)
        )
        if self.shedding_hysteresis_soc != DEFAULT_SHEDDING_HYSTERESIS_SOC:
            self.shedding_hysteresis_soc = checked_shedding_hysteresis(self.shedding_hysteresis_soc)

    @property
    def shedding_thresholds(self) -> tuple[float, float, float, float]:
        """SoC thresholds (percent) of shedding levels 1-4."""
        return self.shedding_l1_soc, self.shedding_l2_soc, self.shedding_l3_soc, self.shedding_l4_soc

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
            env_name, val = _lookup_env(f.name.upper(), env_map)
            if val is not None:
                _assign_coerced(kwargs, f.name, val, f.type, f"environment variable {env_name}")

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
                _assign_coerced(kwargs, f.name, yaml_values[f.name], f.type, f"{yaml_path} key {f.name}")
            # Layer 2: Environment variable override
            env_name, val = _lookup_env(f.name.upper(), env_map)
            if val is not None:
                _assign_coerced(kwargs, f.name, val, f.type, f"environment variable {env_name}")

        sources.append("env")
        cfg = cls(**kwargs)
        cfg._loaded_from = sources
        return cfg

    def is_simulation(self) -> bool:
        """Check if HAL is running in simulation mode."""
        return self.hal_mode == HALMode.SIMULATION

    def get_shedding_level(self, battery_soc: float, previous_level: int | None = None) -> int:
        """Determine load shedding level from battery State-of-Charge.

        Stateless callers (a dashboard label, a recommendation) omit ``previous_level`` and
        get the plain mapping. A caller that tracks the level over time passes the level it
        decided last: the level is then left only once SoC is ``shedding_hysteresis_soc``
        points above its threshold (see ``compute_shedding_level``).

        Returns:
            0 (L0 Nominal) through 4 (L4 Blackout Safe).
        """
        return compute_shedding_level(
            battery_soc, self.shedding_thresholds, previous_level, self.shedding_hysteresis_soc
        )


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
        "SENTINEL_POLL_INTERVAL": ["SENTINEL_POLL_INTERVAL_SECONDS"],
        "MESH_POLL_INTERVAL": ["MESH_POLL_INTERVAL_SECONDS"],
    }


def _lookup_env(env_key: str, env_map: dict[str, list[str]]) -> tuple[str, str | None]:
    """Returns ``(variable name, value)`` for a field: the canonical name first, then its aliases."""
    val = os.environ.get(env_key)
    if val is not None:
        return env_key, val
    for alias in env_map.get(env_key, []):
        val = os.environ.get(alias)
        if val is not None:
            return alias, val
    return env_key, None


def _assign_coerced(kwargs: dict[str, Any], name: str, value: Any, type_hint: str | type, source: str) -> None:
    """Stores ``value`` coerced to the field type in ``kwargs[name]``.

    A value that cannot be converted (``EXPORTER_PORT=abc``) is ignored with a warning that
    names its source, leaving whatever a lower-precedence layer (or the default) provided:
    one mistyped setting must not make every command fail at configuration load.
    """
    if name in _SHEDDING_THRESHOLD_FIELDS:
        # Not coerced one by one: the four thresholds are one policy, parsed and validated
        # together by checked_shedding_thresholds() in __post_init__ (one unparsable value
        # discards all four, exactly as in the sentinel and the power exporter). A blank or
        # null setting means "not set": it leaves a lower-precedence layer's value in place.
        if value is not None and not (isinstance(value, str) and not value.strip()):
            kwargs[name] = value
        return
    try:
        kwargs[name] = _coerce(value, type_hint)
    except (TypeError, ValueError):
        logger.warning("Ignoring %s: %r is not a valid %s; keeping the previous value", source, value, type_hint)


def _coerce(value: Any, type_hint: str | type) -> Any:
    """Coerce a string or raw value to the declared field type.

    Raises:
        ValueError: if a string cannot be parsed as the declared numeric type.
    """
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
