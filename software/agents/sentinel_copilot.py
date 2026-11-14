#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Energy-Aware Datacenter Sentinel Copilot
Polls the power exporter, maps battery state of charge and solar power to an operating
mode (NORMAL, SOLAR_SURPLUS_COMPUTE, ECO_PRESERVATION), writes that mode to a shared mode
file and publishes it on the in-process event bus. It schedules, throttles and stops
nothing itself: other components (e.g. knowledge_indexer) read the mode and act on it.
"""

import logging
import math
import os
import time
import urllib.error
import urllib.request

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
    approximated as UTC + ``SOLAR_UTC_OFFSET_HOURS``.
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


def set_mode(mode: str) -> bool:
    """Publishes the mode on the event bus and writes it to the shared mode file.

    Returns True if the mode file was written. False means other processes still read the
    previous mode: the caller must retry (``run_copilot`` does, on every poll).
    """
    level_map = {
        "NORMAL": 0,
        "SOLAR_SURPLUS_COMPUTE": 0,
        "ECO_PRESERVATION": 2,
        "CRITICAL_SHEDDING": 3,
    }
    level = level_map.get(mode, 2 if "ECO" in mode or "SHED" in mode else 0)
    try:
        from sovereign_dc.events import Event, EventType, get_event_bus

        get_event_bus().publish(
            Event(
                event_type=EventType.LOAD_SHEDDING_CHANGED,
                source="sentinel_copilot",
                payload={"mode": mode, "level": level},
            )
        )
    except Exception as e:
        logger.debug("Event bus notification notice: %s", e)

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


def evaluate_mode(metrics: dict[str, float], forecast: float) -> tuple[str, str] | None:
    """Maps telemetry to the target operating mode and the reason for it.

    Returns None when the battery state of charge is unknown (exporter unreachable,
    series missing or non-finite): the caller must then keep the current mode. Healthy
    values are never substituted for missing telemetry. An unknown solar reading can
    never unlock SOLAR_SURPLUS_COMPUTE.
    """
    soc = _finite_metric(metrics, SOC_METRIC)
    if soc is None:
        return None
    solar = _finite_metric(metrics, SOLAR_METRIC)

    # Predictive load shedding:
    # If battery is currently somewhat okay (e.g. < 40%) but forecast is very low (e.g. night time),
    # preemptively shed load to preserve battery through the night.
    if soc < 25.0:
        return "ECO_PRESERVATION", f"Battery SoC at {soc:.1f}%"
    if soc < 40.0 and forecast < 100.0:
        return "ECO_PRESERVATION", f"Predictive (SoC {soc:.1f}%, Forecast {forecast:.0f}W)"
    if solar is not None and solar > 1000.0 and soc > 75.0:
        return "SOLAR_SURPLUS_COMPUTE", f"{solar:.0f}W, {soc:.1f}% SoC"
    solar_text = f"{solar:.0f}W" if solar is not None else "solar unknown"
    return "NORMAL", f"{solar_text}, {soc:.1f}% SoC, Forecast {forecast:.0f}W"


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

    while True:
        metrics = get_telemetry()
        forecast = get_solar_forecast()
        decision = evaluate_mode(metrics, forecast)

        if write_failing and (decision is None or decision[0] == current_mode):
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
            target_mode, reason = decision
            if target_mode != current_mode or (file_mode != target_mode and not write_failing):
                current_mode = target_mode
                # None (a stand-in that returns nothing) counts as written.
                if set_mode(current_mode) is False:
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

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    run_copilot()
