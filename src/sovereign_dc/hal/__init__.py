"""Sovereign Mini Datacenter — Hardware Abstraction Layer (HAL).

Provides a clean interface between software agents and physical hardware,
with pluggable simulation and hardware backends. This eliminates scattered
inline fallback logic and makes the simulation/hardware boundary testable.
"""

from sovereign_dc.hal.gpu import GPUInfo, detect_gpus
from sovereign_dc.hal.power import PowerReading, read_power
from sovereign_dc.hal.storage import StorageInfo, detect_storage
from sovereign_dc.hal.thermal import ThermalReading, read_thermal

__all__ = [
    "GPUInfo",
    "detect_gpus",
    "PowerReading",
    "read_power",
    "StorageInfo",
    "detect_storage",
    "ThermalReading",
    "read_thermal",
]
