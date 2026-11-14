"""Sovereign Mini Datacenter — HAL Thermal Monitoring Module.

Reads temperature data from the power exporter's Prometheus endpoint only (in hardware
mode the exporter re-exports the ESP32 bridge's DS18B20 probes; this module reads no
1-Wire bus itself), plus Linux thermal zones and nvidia-smi for local chip temperatures.
In simulation mode an unreachable exporter yields simulated values flagged as such.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sovereign_dc.hal.power import METRIC_SIMULATED, fetch_exporter_metrics, optional_metric, required_metric

logger = logging.getLogger("HAL.Thermal")


def read_linux_thermal_zones() -> dict[str, float]:
    """Read native Linux sysfs thermal zones and NVIDIA GPU temperature if available."""
    readings: dict[str, float] = {}
    thermal_root = Path("/sys/class/thermal")
    if thermal_root.exists():
        for zone in sorted(thermal_root.glob("thermal_zone*")):
            try:
                ztype = (zone / "type").read_text(encoding="utf-8").strip()
                raw_milli = int((zone / "temp").read_text(encoding="utf-8").strip())
                readings[f"{zone.name}:{ztype}"] = round(raw_milli / 1000.0, 2)
            except Exception as e:
                logger.debug("Thermal zone read skipped: %s", e)
    if shutil.which("nvidia-smi"):
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                readings["gpu0:nvidia"] = float(res.stdout.strip().splitlines()[0].strip())
        except Exception:
            pass
    return readings


METRIC_COOLANT = "sovereign_temp_coolant_celsius"
METRIC_RACK_INLET = "sovereign_temp_rack_inlet_celsius"
METRIC_RACK_EXHAUST = "sovereign_temp_rack_exhaust_celsius"
OVERTEMP_COOLANT_CELSIUS = 55.0


@dataclass
class ThermalReading:
    """Instantaneous thermal sensor readings.

    ``coolant_celsius`` is always a finite measurement (or a flagged simulated value). The
    rack air temperatures are ``None`` when the source did not report them; they are never
    filled in from the coolant temperature.
    """

    coolant_celsius: float
    rack_inlet_celsius: float | None = None
    rack_exhaust_celsius: float | None = None
    is_simulated: bool = False

    @property
    def thermal_delta(self) -> float | None:
        """Temperature differential across the rack (exhaust - inlet); None if either is unknown."""
        if self.rack_inlet_celsius is None or self.rack_exhaust_celsius is None:
            return None
        return self.rack_exhaust_celsius - self.rack_inlet_celsius

    @property
    def is_overtemp(self) -> bool:
        """Check if coolant temperature exceeds safe operating threshold (55°C)."""
        return self.coolant_celsius > OVERTEMP_COOLANT_CELSIUS


def read_thermal(
    exporter_url: str = "http://localhost:9101/metrics",
    simulation: bool = True,
) -> ThermalReading:
    """Read current thermal data from hardware or simulation.

    The coolant temperature is the required measurement: if the exporter is unreachable,
    reports its source down, or reports a missing or non-finite coolant temperature, there is
    no reading. In hardware mode (``simulation=False``) that raises; in simulation mode
    simulated values are returned with ``is_simulated=True``.

    Args:
        exporter_url: URL of the Prometheus power exporter.
        simulation: If True and no measurement is available, return simulated values.

    Returns:
        ThermalReading with current temperature data.
    """
    try:
        metrics = fetch_exporter_metrics(exporter_url)
        return ThermalReading(
            coolant_celsius=required_metric(metrics, METRIC_COOLANT),
            rack_inlet_celsius=optional_metric(metrics, METRIC_RACK_INLET),
            rack_exhaust_celsius=optional_metric(metrics, METRIC_RACK_EXHAUST),
            is_simulated=metrics.get(METRIC_SIMULATED, 0.0) > 0,
        )
    except Exception as e:
        if simulation:
            logger.debug("Thermal exporter unavailable, using simulated values: %s", e)
            return ThermalReading(
                coolant_celsius=28.0,
                rack_inlet_celsius=22.0,
                rack_exhaust_celsius=30.0,
                is_simulated=True,
            )
        logger.warning("Thermal exporter unavailable and simulation disabled: %s", e)
        raise
