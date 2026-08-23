"""Sovereign Mini Datacenter — HAL Storage Detection Module.

Detects NVMe/ZFS storage capacity and health, or provides simulation fallbacks.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass

logger = logging.getLogger("HAL.Storage")


@dataclass
class StorageInfo:
    """Detected storage subsystem information."""

    total_gb: float
    used_gb: float
    free_gb: float

    @property
    def usage_percent(self) -> float:
        """Storage utilization percentage."""
        if self.total_gb == 0:
            return 0.0
        return (self.used_gb / self.total_gb) * 100.0

    @property
    def is_critically_low(self) -> bool:
        """Check if free space is below 5% of total capacity."""
        return self.usage_percent > 95.0


def detect_storage(
    path: str | None = None,
    simulation: bool = True,
) -> StorageInfo:
    """Detect storage capacity at the given path.

    Args:
        path: Filesystem path to query. Defaults to current working directory.
        simulation: If True and detection fails, return simulated NVMe specs.

    Returns:
        StorageInfo with total, used, and free capacity.
    """
    target_path = path or os.getcwd()
    try:
        total, used, free = shutil.disk_usage(target_path)
        info = StorageInfo(
            total_gb=round(total / (1024**3), 1),
            used_gb=round(used / (1024**3), 1),
            free_gb=round(free / (1024**3), 1),
        )
        logger.debug(
            "Storage detected at %s: %.1f GB total, %.1f GB free",
            target_path,
            info.total_gb,
            info.free_gb,
        )
        return info
    except Exception as e:
        if simulation:
            logger.debug("Storage detection failed, using simulated values: %s", e)
            return StorageInfo(total_gb=4000.0, used_gb=400.0, free_gb=3600.0)
        logger.warning("Storage detection failed and simulation disabled: %s", e)
        raise
