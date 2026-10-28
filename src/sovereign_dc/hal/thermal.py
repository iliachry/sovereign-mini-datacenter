"""Sovereign Mini Datacenter — HAL Thermal Monitoring Module.

Reads temperature data from:
- DS18B20 1-Wire sensors (hardware mode)
- Prometheus exporter endpoint
- Simulated values (simulation mode)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

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


@dataclass
class ThermalReading:
    """Instantaneous thermal sensor readings."""

    coolant_celsius: float
    rack_inlet_celsius: float
    rack_exhaust_celsius: float
    is_simulated: bool = False

    @property
    def thermal_delta(self) -> float:
        """Temperature differential across the rack (exhaust - inlet)."""
        return self.rack_exhaust_celsius - self.rack_inlet_celsius

    @property
    def is_overtemp(self) -> bool:
        """Check if coolant temperature exceeds safe operating threshold (55°C)."""
        return self.coolant_celsius > 55.0


def read_thermal(
    exporter_url: str = "http://localhost:9101/metrics",
    simulation: bool = True,
) -> ThermalReading:
    """Read current thermal data from hardware or simulation.

    Args:
        exporter_url: URL of the Prometheus power exporter.
        simulation: If True and exporter is unavailable, return simulated values.

    Returns:
        ThermalReading with current temperature data.
    """
    try:
        req = urllib.request.Request(exporter_url, headers={"User-Agent": "smdc-hal"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            content = resp.read().decode("utf-8")
            metrics: dict[str, float] = {}
            for line in content.split("\n"):
                if line and not line.startswith("#"):
                    parts = line.split()
                    if len(parts) == 2:
                        try:
                            metrics[parts[0]] = float(parts[1])
                        except ValueError:
                            pass
            return ThermalReading(
                coolant_celsius=metrics.get("sovereign_temp_coolant_celsius", 28.0),
                rack_inlet_celsius=metrics.get("sovereign_temp_rack_inlet_celsius", 22.0),
                rack_exhaust_celsius=metrics.get("sovereign_temp_rack_exhaust_celsius", 30.0),
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
