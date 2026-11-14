"""Sovereign Mini Datacenter — HAL Power Telemetry Module.

Reads battery SoC, solar PV power and system load from the power exporter's
Prometheus endpoint only. In hardware mode the exporter itself re-exports the ESP32
telemetry bridge (VE.Direct); this module opens no serial port and speaks no Modbus.
In simulation mode an unreachable exporter yields simulated values flagged as such.
"""

from __future__ import annotations

import logging
import math
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger("HAL.Power")


# Exporter metric names (Prometheus text exposition) read by the HAL.
METRIC_SOURCE_UP = "sovereign_telemetry_source_up"
METRIC_SIMULATED = "sovereign_telemetry_simulated"
METRIC_BATTERY_SOC = "sovereign_battery_soc_percent"
METRIC_SOLAR_WATTS = "sovereign_solar_pv_power_watts"
METRIC_BATTERY_VOLTAGE = "sovereign_battery_voltage_volts"
METRIC_SYSTEM_LOAD = "sovereign_system_power_draw_watts"
METRIC_LOAD_SHEDDING = "sovereign_load_shedding_active"


class TelemetryUnavailable(RuntimeError):
    """The exporter answered but holds no usable measurement (source down, missing or non-finite value)."""


@dataclass
class PowerReading:
    """Instantaneous power system telemetry reading.

    ``battery_soc`` is always a finite measurement (or a flagged simulated value). The other
    quantities are ``None`` when the source did not report them: a missing value is never
    replaced by a nominal constant.
    """

    battery_soc: float
    solar_watts: float | None
    battery_voltage: float | None = None
    system_load_watts: float | None = None
    load_shedding_active: bool = False
    is_simulated: bool = False

    @property
    def net_power(self) -> float | None:
        """Net power flow (positive = charging, negative = discharging); None if either input is unknown."""
        if self.solar_watts is None or self.system_load_watts is None:
            return None
        return self.solar_watts - self.system_load_watts


def fetch_exporter_metrics(exporter_url: str, timeout: float = 2.0) -> dict[str, float]:
    """Fetches and parses the exporter's metrics, refusing a source that reports itself down.

    Raises TelemetryUnavailable when ``sovereign_telemetry_source_up`` is present and is not a
    finite value above zero, or when the provenance flag ``sovereign_telemetry_simulated`` is
    present but not finite: in both cases the other samples are not measurements.
    """
    req = urllib.request.Request(exporter_url, headers={"User-Agent": "smdc-hal"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        metrics = _parse_prometheus_metrics(resp.read().decode("utf-8"))
    if METRIC_SOURCE_UP in metrics:
        source_up = metrics[METRIC_SOURCE_UP]
        if not math.isfinite(source_up) or source_up <= 0:
            raise TelemetryUnavailable(
                "exporter reports its telemetry source is down (sovereign_telemetry_source_up 0)"
            )
    if METRIC_SIMULATED in metrics and not math.isfinite(metrics[METRIC_SIMULATED]):
        raise TelemetryUnavailable("exporter reports a non-finite sovereign_telemetry_simulated flag")
    return metrics


def required_metric(metrics: dict[str, float], name: str) -> float:
    """The finite value of ``name``; TelemetryUnavailable if it is missing, NaN or infinite."""
    value = metrics.get(name)
    if value is None:
        raise TelemetryUnavailable(f"exporter reports no {name} measurement")
    if not math.isfinite(value):
        raise TelemetryUnavailable(f"exporter reports a non-finite {name} ({value})")
    return value


def optional_metric(metrics: dict[str, float], name: str) -> float | None:
    """The finite value of ``name``, or None when it is missing, NaN or infinite."""
    value = metrics.get(name)
    if value is None or not math.isfinite(value):
        return None
    return value


def read_power(
    exporter_url: str = "http://localhost:9101/metrics",
    simulation: bool = True,
) -> PowerReading:
    """Read current power telemetry from hardware or simulation.

    The battery state of charge is the required measurement: if the exporter is unreachable,
    reports its source down, or reports a missing or non-finite SoC, there is no reading.
    In hardware mode (``simulation=False``) that raises; in simulation mode simulated values
    are returned with ``is_simulated=True``. Solar power, battery voltage and system load are
    ``None`` when the exporter does not report a finite value for them.

    Args:
        exporter_url: URL of the Prometheus power exporter.
        simulation: If True and no measurement is available, return simulated values.

    Returns:
        PowerReading with current battery and solar state.
    """
    try:
        metrics = fetch_exporter_metrics(exporter_url)
        return PowerReading(
            battery_soc=required_metric(metrics, METRIC_BATTERY_SOC),
            solar_watts=optional_metric(metrics, METRIC_SOLAR_WATTS),
            battery_voltage=optional_metric(metrics, METRIC_BATTERY_VOLTAGE),
            system_load_watts=optional_metric(metrics, METRIC_SYSTEM_LOAD),
            load_shedding_active=(optional_metric(metrics, METRIC_LOAD_SHEDDING) or 0.0) > 0,
            is_simulated=metrics.get(METRIC_SIMULATED, 0.0) > 0,
        )
    except Exception as e:
        if simulation:
            logger.debug("Power exporter unavailable, using simulated values: %s", e)
            return PowerReading(
                battery_soc=88.5,
                solar_watts=1240.0,
                battery_voltage=53.2,
                system_load_watts=280.0,
                load_shedding_active=False,
                is_simulated=True,
            )
        logger.warning("Power exporter unavailable and simulation disabled: %s", e)
        raise


def _parse_prometheus_metrics(content: str) -> dict[str, float]:
    """Parse Prometheus text exposition format into a metric name → value dict.

    Non-finite samples (``NaN``, ``+Inf``) are kept as such so callers can tell "reported as
    unavailable" from "not reported"; they are never treated as measurements.
    """
    metrics: dict[str, float] = {}
    for line in content.split("\n"):
        if line and not line.startswith("#"):
            parts = line.split()
            if len(parts) == 2:
                try:
                    metrics[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return metrics
