#!/usr/bin/env python3
"""
Sovereign Mini Datacenter - Energy-Aware Datacenter Sentinel Copilot
Optimizes background AI jobs, model fine-tuning, and batch vectorization based on solar yield.
"""

import logging
import math
import os
import time
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SentinelCopilot] %(message)s")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
POWER_EXPORTER_URL = os.getenv("POWER_EXPORTER_URL", "http://power-exporter:9101/metrics")
# Shared mode file read by other agents (e.g. knowledge_indexer). When the agents run in
# separate containers this path must be on a volume mounted into each of them.
MODE_FILE = os.getenv("SOVEREIGN_MODE_FILE", "/tmp/sovereign_mode")
# Offset (hours, east positive) added to UTC to approximate local solar time for the
# forecast. Default 0 = the site is assumed to be on the Greenwich meridian; set it to
# site_longitude_deg / 15 (e.g. 1.58 for Athens).
SOLAR_UTC_OFFSET_HOURS = float(os.getenv("SOLAR_UTC_OFFSET_HOURS", "0") or 0.0)
POLL_INTERVAL_SECONDS = 30

SOC_METRIC = "sovereign_battery_soc_percent"
SOLAR_METRIC = "sovereign_solar_pv_power_watts"

DEFAULT_MODE = "NORMAL"
VALID_MODES = frozenset({"NORMAL", "SOLAR_SURPLUS_COMPUTE", "ECO_PRESERVATION", "CRITICAL_SHEDDING"})


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
        logging.warning("Could not reach power exporter: %s", e)
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
        return daylight * 1500.0  # Max 1500W predicted
    return 0.0


def set_mode(mode: str) -> None:
    """Writes the current mode to a shared state file and dispatches event."""
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
        logging.debug("Event bus notification notice: %s", e)

    mode_file = os.getenv("SOVEREIGN_MODE_FILE", MODE_FILE)
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
        logging.warning("Failed to write mode state: %s", e)


def read_persisted_mode() -> str | None:
    """Returns the mode stored in the shared mode file, or None if absent or not a valid mode.

    The file outlives this process (it sits on a volume shared with other agents), so a
    restarted sentinel must resume from it instead of assuming NORMAL.
    """
    mode_file = os.getenv("SOVEREIGN_MODE_FILE", MODE_FILE)
    try:
        with open(mode_file, encoding="utf-8") as f:
            mode = f.read(64).strip()
    except FileNotFoundError:
        return None
    except Exception as e:
        logging.warning("Failed to read persisted mode state: %s", e)
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
    logging.info("Starting Energy-Aware Datacenter Sentinel Copilot...")
    persisted_mode = read_persisted_mode()
    current_mode = persisted_mode or DEFAULT_MODE
    if persisted_mode is not None:
        logging.info("Resuming from persisted mode %s.", persisted_mode)
    # The mode file is only known to match the in-memory mode once this process has
    # written it. Until then the first evaluation writes it even if the mode is unchanged,
    # so a stale file (e.g. ECO_PRESERVATION left by a previous run) cannot survive.
    mode_file_synced = False

    while True:
        metrics = get_telemetry()
        forecast = get_solar_forecast()
        decision = evaluate_mode(metrics, forecast)

        if decision is None:
            logging.warning(
                "Battery telemetry unavailable (%s missing); holding mode %s. No default values are assumed.",
                SOC_METRIC,
                current_mode,
            )
        else:
            target_mode, reason = decision
            if target_mode != current_mode or not mode_file_synced:
                current_mode = target_mode
                set_mode(current_mode)
                mode_file_synced = True
                if target_mode == "ECO_PRESERVATION":
                    logging.warning(
                        "⚡ Sentinel Trigger: %s. Throttling non-essential AI batch jobs to conserve power!", reason
                    )
                elif target_mode == "SOLAR_SURPLUS_COMPUTE":
                    logging.info(
                        "☀️ Solar Surplus (%s): Unlocking full GPU compute capacity for model training & batch vectorization.",
                        reason,
                    )
                else:
                    logging.info("Nominal operating conditions (%s).", reason)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_copilot()
