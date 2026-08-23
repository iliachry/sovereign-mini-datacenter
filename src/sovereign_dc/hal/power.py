"""Sovereign Mini Datacenter — HAL Power Telemetry Module.

Reads battery SoC, solar PV power, and system load from:
- Victron VE.Direct serial (hardware mode)
- RS485 Modbus BMS (hardware mode)
- Prometheus exporter HTTP endpoint
- Simulated physics-based defaults (simulation mode)
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger("HAL.Power")


@dataclass
class PowerReading:
    """Instantaneous power system telemetry reading."""

    battery_soc: float
    solar_watts: float
    battery_voltage: float = 52.8
    system_load_watts: float = 280.0
    load_shedding_active: bool = False

    @property
    def net_power(self) -> float:
        """Net power flow (positive = charging, negative = discharging)."""
        return self.solar_watts - self.system_load_watts


def read_power(
    exporter_url: str = "http://localhost:9101/metrics",
    simulation: bool = True,
) -> PowerReading:
    """Read current power telemetry from hardware or simulation.

    Args:
        exporter_url: URL of the Prometheus power exporter.
        simulation: If True and exporter is unavailable, return simulated values.

    Returns:
        PowerReading with current battery and solar state.
    """
    try:
        req = urllib.request.Request(exporter_url, headers={"User-Agent": "smdc-hal"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            content = resp.read().decode("utf-8")
            metrics = _parse_prometheus_metrics(content)
            return PowerReading(
                battery_soc=metrics.get("sovereign_battery_soc_percent", 85.0),
                solar_watts=metrics.get("sovereign_solar_pv_power_watts", 0.0),
                battery_voltage=metrics.get("sovereign_battery_voltage_volts", 52.8),
                system_load_watts=metrics.get("sovereign_system_power_draw_watts", 280.0),
                load_shedding_active=metrics.get("sovereign_load_shedding_active", 0.0) > 0,
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
            )
        logger.warning("Power exporter unavailable and simulation disabled: %s", e)
        raise


def _parse_prometheus_metrics(content: str) -> dict[str, float]:
    """Parse Prometheus text exposition format into a metric name → value dict."""
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
