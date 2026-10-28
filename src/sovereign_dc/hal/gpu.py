"""Sovereign Mini Datacenter — HAL GPU Detection Module.

Detects NVIDIA GPU accelerators via nvidia-smi or provides simulation fallbacks
for DGX Spark / Jetson Orin platforms.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("HAL.GPU")


def _read_unified_memory_mb() -> str:
    """Resolve unified memory capacity from /proc/meminfo for Grace Blackwell / Jetson SoCs."""
    try:
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            for line in meminfo.read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return str(int(parts[1]) // 1024)
    except Exception:
        pass
    return "[N/A]"


@dataclass
class GPUInfo:
    """Detected GPU accelerator information."""

    name: str
    memory_mb: str
    status: str = "Ready"


def detect_gpus(simulation: bool = True) -> list[GPUInfo]:
    """Detect GPU accelerators on the system.

    Args:
        simulation: If True and no real GPU is found, return simulated DGX/Jetson specs.

    Returns:
        List of detected or simulated GPU information.
    """
    gpus: list[GPUInfo] = []

    if shutil.which("nvidia-smi"):
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.strip().split("\n"):
                    parts = line.split(",")
                    name = parts[0].strip()
                    mem = parts[1].strip() if len(parts) > 1 else "Unknown"
                    if mem in ("[N/A]", "N/A", "[Not Supported]"):
                        mem = _read_unified_memory_mb()
                    gpus.append(GPUInfo(name=name, memory_mb=mem))
                logger.info("Detected %d NVIDIA GPU(s) via nvidia-smi", len(gpus))
                return gpus
        except Exception as e:
            logger.warning("nvidia-smi query failed: %s", e)

    if simulation:
        gpus.append(
            GPUInfo(
                name="NVIDIA DGX Spark / Jetson Orin Industrial (275 TOPS)",
                memory_mb="65536",
                status="Simulated",
            )
        )
        logger.debug("Using simulated GPU profile (DGX Spark / Jetson Orin)")

    return gpus
