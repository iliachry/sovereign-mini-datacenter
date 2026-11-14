#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Energy-Aware Datacenter Sentinel Copilot
Polls the power exporter, maps battery state of charge (SoC) to a load-shedding level and
an operating mode, writes the mode to a shared mode file and publishes mode, level and SoC
on the in-process event bus. It schedules, throttles and stops nothing itself: other
components (the knowledge indexer reads the mode file, the enterprise manager reacts to
the event) act on what it publishes.

Shedding level (the single SoC policy of the node; identical to
``sovereign_dc.config.SovereignConfig.get_shedding_level``):

    level 0   SoC >= SHEDDING_L1_SOC               (default 50 %)
    level 1   SoC <  SHEDDING_L1_SOC               (default 50 %)
    level 2   SoC <  SHEDDING_L2_SOC               (default 30 %)
    level 3   SoC <  SHEDDING_L3_SOC               (default 20 %)
    level 4   SoC <  SHEDDING_L4_SOC               (default 10 %)

The thresholds must be numbers within 0-100 with L1 > L2 > L3 > L4; otherwise one warning
is logged and all four defaults are used. The running sentinel tracks the level with a
hysteresis: a level is entered as soon as SoC is below its threshold, and left only once
SoC is SHEDDING_HYSTERESIS_SOC points (default 2) above it, so a SoC hovering at a
threshold does not toggle the level on every poll.

Mode, derived from the level:

    level 0-1  NORMAL, or SOLAR_SURPLUS_COMPUTE at level 0 while solar power is above
               1000 W and SoC is above 75 %
    level 2-4  ECO_PRESERVATION

This file is also shipped as a standalone, standard-library-only script
(software/agents/sentinel_copilot.py, byte-identical), so it cannot import
``sovereign_dc.config``: it reads the same ``SHEDDING_L1_SOC`` .. ``SHEDDING_L4_SOC`` and
``SHEDDING_HYSTERESIS_SOC`` environment variables with the same defaults, and carries a
byte-identical copy of the policy functions (tests/ compares the two).
"""

import logging
import math
import os
import time
import urllib.error
import urllib.request
from typing import NamedTuple

# Logging is configured only when this file runs as a script (see the bottom of the file):
# importing it as a library must not touch the root logger.
logger = logging.getLogger("SentinelCopilot")
LOG_FORMAT = "%(asctime)s [SentinelCopilot] %(message)s"

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
POWER_EXPORTER_URL = os.getenv("POWER_EXPORTER_URL", "http://power-exporter:9101/metrics")
# Shared mode file read by other agents (e.g. knowledge_indexer). When the agents run in
# separate containers this path must be on a volume mounted into each of them. Empty means
# "use the default in the private state directory" (see mode_file_path()).
MODE_FILE = os.getenv("SOVEREIGN_MODE_FILE", "")
# Offset (hours, east positive) added to UTC to approximate local solar time for the
# forecast. Default 0 = the site is assumed to be on the Greenwich meridian; set it to
# site_longitude_deg / 15 (e.g. 1.58 for Athens).
SOLAR_UTC_OFFSET_HOURS = float(os.getenv("SOLAR_UTC_OFFSET_HOURS", "0") or 0.0)
DEFAULT_POLL_INTERVAL_SECONDS = 30.0
MIN_POLL_INTERVAL_SECONDS = 1.0


def poll_interval_seconds(raw: str | None = None) -> float:
    """Seconds between telemetry polls: ``SENTINEL_POLL_INTERVAL_SECONDS`` (default 30).

    A value that is not a finite number of at least ``MIN_POLL_INTERVAL_SECONDS`` is
    rejected with a warning and the default is used, so a typo can neither stop the
    sentinel nor turn its loop into a busy loop against the power exporter.
    """
    value = os.getenv("SENTINEL_POLL_INTERVAL_SECONDS", "") if raw is None else raw
    if not value.strip():
        return DEFAULT_POLL_INTERVAL_SECONDS
    try:
        seconds = float(value)
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < MIN_POLL_INTERVAL_SECONDS:
        logger.warning(
            "Ignoring SENTINEL_POLL_INTERVAL_SECONDS=%r (need a number >= %g); using %g s.",
            value,
            MIN_POLL_INTERVAL_SECONDS,
            DEFAULT_POLL_INTERVAL_SECONDS,
        )
        return DEFAULT_POLL_INTERVAL_SECONDS
    return seconds


# How often run_copilot() polls the power exporter. A change in battery state is noticed
# up to this many seconds after it happens.
POLL_INTERVAL_SECONDS = poll_interval_seconds()

SOC_METRIC = "sovereign_battery_soc_percent"
SOLAR_METRIC = "sovereign_solar_pv_power_watts"

DEFAULT_MODE = "NORMAL"
VALID_MODES = frozenset({"NORMAL", "SOLAR_SURPLUS_COMPUTE", "ECO_PRESERVATION", "CRITICAL_SHEDDING"})

# --- shared shedding policy: begin -----------------------------------------------------
# This block is byte-identical in src/sovereign_dc/config.py and
# src/sovereign_dc/agents/sentinel_copilot.py (the sentinel also ships as a standalone,
# standard-library-only script and cannot import the package); tests/ compares the two.
# It needs only ``math`` and the module's ``logger``.
SHEDDING_ENV_NAMES = ("SHEDDING_L1_SOC", "SHEDDING_L2_SOC", "SHEDDING_L3_SOC", "SHEDDING_L4_SOC")
DEFAULT_SHEDDING_THRESHOLDS = (50.0, 30.0, 20.0, 10.0)
SHEDDING_HYSTERESIS_ENV = "SHEDDING_HYSTERESIS_SOC"
DEFAULT_SHEDDING_HYSTERESIS_SOC = 2.0
MAX_SHEDDING_HYSTERESIS_SOC = 10.0


def shedding_policy_problem(thresholds: tuple[float, float, float, float]) -> str | None:
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


def checked_shedding_thresholds(
    thresholds: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """``thresholds`` if they are a usable policy, else the defaults (one warning names why).

    A partly applied policy would be worse than the default one, so one bad value discards
    all four: SHEDDING_L1_SOC > SHEDDING_L2_SOC > SHEDDING_L3_SOC > SHEDDING_L4_SOC.
    """
    problem = shedding_policy_problem(thresholds)
    if problem is None:
        return (float(thresholds[0]), float(thresholds[1]), float(thresholds[2]), float(thresholds[3]))
    logger.warning(
        "Ignoring the load-shedding thresholds %s (%s): they must be numbers within 0-100 with "
        "%s; using the defaults %s.",
        ", ".join(f"{name}={value!r}" for name, value in zip(SHEDDING_ENV_NAMES, thresholds, strict=False)),
        problem,
        " > ".join(SHEDDING_ENV_NAMES),
        " / ".join(f"{value:g}" for value in DEFAULT_SHEDDING_THRESHOLDS),
    )
    return DEFAULT_SHEDDING_THRESHOLDS


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

# SoC thresholds (percent) of shedding levels 1-4: (environment variable, default). The
# names and defaults are those of SovereignConfig.shedding_l1_soc .. shedding_l4_soc.
SHEDDING_SOC_DEFAULTS: tuple[tuple[str, float], ...] = tuple(
    zip(SHEDDING_ENV_NAMES, DEFAULT_SHEDDING_THRESHOLDS, strict=True)
)
# Lowest shedding level at which the mode is ECO_PRESERVATION.
ECO_MODE_MIN_LEVEL = 2
# SOLAR_SURPLUS_COMPUTE needs level 0 plus more solar power and SoC than these.
SURPLUS_MIN_SOLAR_WATTS = 1000.0
SURPLUS_MIN_SOC_PERCENT = 75.0
# Level published by set_mode() for a mode when the caller does not pass the level.
MODE_DEFAULT_LEVELS: dict[str, int] = {
    "NORMAL": 0,
    "SOLAR_SURPLUS_COMPUTE": 0,
    "ECO_PRESERVATION": 2,
    "CRITICAL_SHEDDING": 3,
}
# With mode and level unchanged, the event is published again once SoC has moved by this
# many percentage points, so that subscribers comparing SoC with a per-application floor
# (EnterpriseManager, power.min_battery_soc) see it at this resolution.
SOC_REPUBLISH_DELTA_PERCENT = 1.0


class SheddingState(NamedTuple):
    """One evaluation of the telemetry: target mode, why, shedding level 0-4 and the SoC used."""

    mode: str
    reason: str
    level: int
    soc: float


def shedding_thresholds() -> tuple[float, float, float, float]:
    """SoC thresholds of levels 1-4 from ``SHEDDING_L1_SOC`` .. ``SHEDDING_L4_SOC``.

    Exactly what ``SovereignConfig`` does: a value that cannot be parsed as a number is
    ignored with a warning and that one default is kept; the four values are then checked
    as one policy (finite, within 0-100, L1 > L2 > L3 > L4) and, on a violation, one warning
    is logged and all four defaults are used.
    """
    values: list[float] = []
    for name, default in SHEDDING_SOC_DEFAULTS:
        raw = os.getenv(name, "").strip()
        value = default
        if raw:
            try:
                value = float(raw)
            except ValueError:
                logger.warning("Ignoring %s=%r (need a number); using %g %%.", name, raw, default)
        values.append(value)
    return checked_shedding_thresholds((values[0], values[1], values[2], values[3]))


def shedding_hysteresis() -> float:
    """SoC points of level hysteresis from ``SHEDDING_HYSTERESIS_SOC`` (default 2)."""
    raw = os.getenv(SHEDDING_HYSTERESIS_ENV, "").strip()
    return checked_shedding_hysteresis(raw) if raw else DEFAULT_SHEDDING_HYSTERESIS_SOC


# Read once at start-up, like SovereignConfig: a change needs a restart of the sentinel.
SHEDDING_THRESHOLDS = shedding_thresholds()
SHEDDING_HYSTERESIS = shedding_hysteresis()


def shedding_level(
    soc: float,
    thresholds: tuple[float, float, float, float] | None = None,
    previous_level: int | None = None,
) -> int:
    """Load-shedding level 0-4 for a battery SoC; same rule as ``SovereignConfig.get_shedding_level``.

    ``thresholds`` defaults to ``SHEDDING_THRESHOLDS`` (the environment at start-up). With
    ``previous_level`` the level is left only once SoC is ``SHEDDING_HYSTERESIS`` points
    above its threshold (``compute_shedding_level``); without it this is the plain mapping.
    """
    return compute_shedding_level(
        soc, SHEDDING_THRESHOLDS if thresholds is None else thresholds, previous_level, SHEDDING_HYSTERESIS
    )


def mode_file_path() -> str:
    """Path of the shared Sentinel mode file.

    ``SOVEREIGN_MODE_FILE`` if set (the container deployment points it at a volume shared by
    the agents), else ``sovereign_mode`` inside the private state directory
    (``SMDC_STATE_DIR`` or ``~/.smdc``, mode 0700). The default is never a predictable name
    in shared ``/tmp``, where any local user could pre-create or replace it.
    """
    configured = os.getenv("SOVEREIGN_MODE_FILE") or MODE_FILE
    if configured:
        return configured
    state_dir = os.environ.get("SMDC_STATE_DIR") or os.path.expanduser("~/.smdc")
    try:
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        os.chmod(state_dir, 0o700)
    except OSError as e:
        logger.warning("State directory %s is unusable: %s", state_dir, e)
    return os.path.join(state_dir, "sovereign_mode")


def get_telemetry() -> dict[str, float]:
    try:
        req = urllib.request.Request(POWER_EXPORTER_URL, headers={"User-Agent": "sentinel-copilot"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            lines = resp.read().decode("utf-8").split("\n")
            metrics = {}
            for line in lines:
                if line and not line.startswith("#"):
                    parts = line.split()
                    if len(parts) == 2:
                        metrics[parts[0]] = float(parts[1])
            return metrics
    except Exception as e:
        logger.warning("Could not reach power exporter: %s", e)
        return {}


def get_solar_forecast() -> float:
    """Returns a mocked 4-hour forward prediction of solar yield (Watts) based on time of day.

    This is a clear-sky bell curve, not a weather forecast. Local solar time is
    approximated as UTC + ``SOLAR_UTC_OFFSET_HOURS``. The value is informational: it is
    logged with the decision and changes neither the mode nor the shedding level.
    """
    # In a real scenario, this would query Open-Meteo or local atmospheric models.
    hour = (time.time() / 3600 + SOLAR_UTC_OFFSET_HOURS) % 24
    future_hour = (hour + 4.0) % 24

    # Simple bell curve for daylight between 6am and 8pm (20:00)
    if 6 <= future_hour <= 20:
        daylight = max(0.0, math.sin(math.pi * (future_hour - 6) / 14))
        return daylight * 3000.0  # Max 3000W predicted (3,280W array)
    return 0.0


def write_mode_file(mode: str, log_failure: bool = True) -> bool:
    """Writes ``mode`` to the shared mode file. Returns True only if the file now holds it.

    A failure is logged as a warning unless ``log_failure`` is False (the caller is
    retrying and has already reported this failure streak).
    """
    mode_file = mode_file_path()
    try:
        directory = os.path.dirname(mode_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # Write-then-rename so a concurrent reader never sees a partial mode string.
        tmp_file = f"{mode_file}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(mode)
        os.replace(tmp_file, mode_file)
    except Exception as e:
        if log_failure:
            logger.warning("Failed to write mode state %s to %s: %s", mode, mode_file, e)
        return False
    return True


def publish_shedding(mode: str, level: int, soc: float | None = None) -> None:
    """Publishes ``load_shedding.changed`` on the in-process event bus (best effort).

    Payload: ``mode``, ``level`` (0-4) and, when known, ``battery_soc`` (percent). In the
    standalone container ``sovereign_dc`` is not installed and nothing is published: there
    the mode file is the only output.
    """
    payload: dict[str, object] = {"mode": mode, "level": level}
    if soc is not None:
        payload["battery_soc"] = soc
    try:
        from sovereign_dc.events import Event, EventType, get_event_bus

        get_event_bus().publish(
            Event(event_type=EventType.LOAD_SHEDDING_CHANGED, source="sentinel_copilot", payload=payload)
        )
    except Exception as e:
        logger.debug("Event bus notification notice: %s", e)


def set_mode(mode: str, level: int | None = None, soc: float | None = None) -> bool:
    """Publishes the mode on the event bus and writes it to the shared mode file.

    ``level`` is the shedding level to publish with it; when omitted, the level implied by
    the mode alone is used (``MODE_DEFAULT_LEVELS``: ECO_PRESERVATION -> 2).

    Returns True if the mode file was written. False means other processes still read the
    previous mode: the caller must retry (``run_copilot`` does, on every poll).
    """
    if level is None:
        level = MODE_DEFAULT_LEVELS.get(mode, ECO_MODE_MIN_LEVEL if "ECO" in mode or "SHED" in mode else 0)
    publish_shedding(mode, level, soc)
    return write_mode_file(mode)


def read_persisted_mode() -> str | None:
    """Returns the mode stored in the shared mode file, or None if absent or not a valid mode.

    The file outlives this process (it sits on a volume shared with other agents), so a
    restarted sentinel must resume from it instead of assuming NORMAL.
    """
    mode_file = mode_file_path()
    try:
        with open(mode_file, encoding="utf-8") as f:
            mode = f.read(64).strip()
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("Failed to read persisted mode state: %s", e)
        return None
    return mode if mode in VALID_MODES else None


def _finite_metric(metrics: dict[str, float], name: str) -> float | None:
    value = metrics.get(name)
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def evaluate_state(
    metrics: dict[str, float], forecast: float, previous_level: int | None = None
) -> SheddingState | None:
    """Maps telemetry to the shedding level, the target operating mode and the reason for it.

    ``previous_level`` is the level of the previous evaluation, when the caller tracks one
    (``run_copilot`` does): the level, and the mode derived from it, then recover with the
    hysteresis of ``shedding_level``. Without it the plain mapping is used.

    Returns None when the battery state of charge is unknown (exporter unreachable,
    series missing or non-finite): the caller must then keep the current mode. Healthy
    values are never substituted for missing telemetry. An unknown solar reading can
    never unlock SOLAR_SURPLUS_COMPUTE. ``forecast`` only appears in the reason text.
    """
    soc = _finite_metric(metrics, SOC_METRIC)
    if soc is None:
        return None
    solar = _finite_metric(metrics, SOLAR_METRIC)
    level = shedding_level(soc, previous_level=previous_level)

    if level >= ECO_MODE_MIN_LEVEL:
        return SheddingState("ECO_PRESERVATION", f"Battery SoC at {soc:.1f}% (shedding level {level})", level, soc)
    if level == 0 and solar is not None and solar > SURPLUS_MIN_SOLAR_WATTS and soc > SURPLUS_MIN_SOC_PERCENT:
        return SheddingState("SOLAR_SURPLUS_COMPUTE", f"{solar:.0f}W, {soc:.1f}% SoC", level, soc)
    solar_text = f"{solar:.0f}W" if solar is not None else "solar unknown"
    reason = f"{solar_text}, {soc:.1f}% SoC, shedding level {level}, Forecast {forecast:.0f}W"
    return SheddingState("NORMAL", reason, level, soc)


def evaluate_mode(metrics: dict[str, float], forecast: float) -> tuple[str, str] | None:
    """``(mode, reason)`` of ``evaluate_state``, or None when the battery SoC is unknown."""
    state = evaluate_state(metrics, forecast)
    return None if state is None else (state.mode, state.reason)


def run_copilot() -> None:
    logger.info("Starting Energy-Aware Datacenter Sentinel Copilot (poll every %g s)...", POLL_INTERVAL_SECONDS)
    persisted_mode = read_persisted_mode()
    current_mode = persisted_mode or DEFAULT_MODE
    if persisted_mode is not None:
        logger.info("Resuming from persisted mode %s.", persisted_mode)
    # The decided mode (current_mode) and the mode the file is known to hold are tracked
    # separately. The file is only known to match once this process has written it, so the
    # first evaluation writes it even if the mode is unchanged (a stale ECO_PRESERVATION left
    # by a previous run cannot survive), and a write that failed is retried on every poll,
    # with or without fresh telemetry, until it succeeds.
    file_mode: str | None = None
    write_failing = False
    # Level and SoC of the last published event (see SOC_REPUBLISH_DELTA_PERCENT).
    published_level: int | None = None
    published_soc: float | None = None
    # Level of the last evaluation with a SoC reading: the hysteresis of shedding_level()
    # is applied against it. None until the first reading (a restart starts from the plain
    # mapping); an unknown SoC keeps it.
    tracked_level: int | None = None

    while True:
        metrics = get_telemetry()
        forecast = get_solar_forecast()
        decision = evaluate_state(metrics, forecast, tracked_level)
        if decision is not None:
            tracked_level = decision.level

        if write_failing and (decision is None or decision.mode == current_mode):
            # Same decision as before, but the mode file still holds something else.
            if write_mode_file(current_mode, log_failure=False):
                file_mode = current_mode
                write_failing = False
                logger.info("Mode file now holds %s (earlier write failures recovered).", current_mode)

        if decision is None:
            logger.warning(
                "Battery telemetry unavailable (%s missing); holding mode %s. No default values are assumed.",
                SOC_METRIC,
                current_mode,
            )
        else:
            target_mode, reason = decision.mode, decision.reason
            if target_mode != current_mode or (file_mode != target_mode and not write_failing):
                current_mode = target_mode
                published_level, published_soc = decision.level, decision.soc
                # None (a stand-in that returns nothing) counts as written.
                if set_mode(current_mode, decision.level, decision.soc) is False:
                    # write_mode_file() has logged the failure; the retries above stay quiet.
                    write_failing = True
                else:
                    file_mode = current_mode
                    if write_failing:
                        logger.info("Mode file now holds %s (earlier write failures recovered).", current_mode)
                    write_failing = False
                if target_mode == "ECO_PRESERVATION":
                    logger.warning(
                        "⚡ Sentinel Trigger: %s. Mode set to ECO_PRESERVATION (mode readers defer batch work).", reason
                    )
                elif target_mode == "SOLAR_SURPLUS_COMPUTE":
                    logger.info("☀️ Solar Surplus (%s): mode set to SOLAR_SURPLUS_COMPUTE.", reason)
                else:
                    logger.info("Nominal operating conditions (%s).", reason)
            elif (
                decision.level != published_level
                or published_soc is None
                or abs(decision.soc - published_soc) >= SOC_REPUBLISH_DELTA_PERCENT
            ):
                # Same mode, but the level changed or SoC moved: subscribers need the event.
                if decision.level != published_level:
                    logger.info("Shedding level now %d (%s); mode stays %s.", decision.level, reason, current_mode)
                published_level, published_soc = decision.level, decision.soc
                publish_shedding(current_mode, decision.level, decision.soc)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    run_copilot()
