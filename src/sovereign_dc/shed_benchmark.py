"""Load-shedding software-path latency, measured in simulation on the local machine.

Measures how long the code in this repository takes, in one process, to go from a telemetry
sample whose battery state of charge is below the level-2 shedding threshold (30 % by
default, where the sentinel's mode becomes ECO_PRESERVATION) to:

1. the sentinel's mode decision (``sentinel_copilot.evaluate_mode``),
2. the enterprise manager having marked every deferrable application ``PAUSED`` after
   receiving the sentinel's ``load_shedding.changed`` event on the in-process event bus,
3. the mode being published in full (event dispatched and mode file written, i.e.
   ``sentinel_copilot.set_mode`` returned).

What is NOT measured: the sentinel's polling interval, the HTTP scrape of the power
exporter, and every hardware actuation (GPU power cap, cgroup throttle, relay). The
enterprise manager only tracks state; no process is signalled. The numbers are wall-clock
times of Python code on whatever machine runs the benchmark.
"""

from __future__ import annotations

import logging
import math
import os
import platform
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

from sovereign_dc.agents import sentinel_copilot
from sovereign_dc.enterprise.manager import EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import AppStatus, PowerPriority
from sovereign_dc.events import Event, get_event_bus

# Battery state of charge of the synthetic sample: below the level-2 threshold (SHEDDING_L2_SOC,
# default 30 %), i.e. shedding level 2, the lowest level whose mode is ECO_PRESERVATION.
SHED_SOC_PERCENT = 24.0
SHED_MODE = "ECO_PRESERVATION"
NOMINAL_MODE = "NORMAL"
DEFAULT_ITERATIONS = 200
DEFAULT_APPS = 3
# Loggers that emit one record per paused/resumed app; silenced while timing so console
# I/O is not part of the measurement.
_QUIET_LOGGERS = ("smdc.enterprise.manager", "smdc.enterprise.registry", "SovereignEventBus", "SentinelCopilot")


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an ascending, non-empty list."""
    rank = max(1, math.ceil(len(sorted_values) * fraction))
    return sorted_values[min(len(sorted_values), rank) - 1]


def summarize_ms(samples_seconds: list[float]) -> dict[str, float]:
    """Median, p95 (nearest rank), min and max of ``samples_seconds``, in milliseconds."""
    ordered = sorted(samples_seconds)
    return {
        "median_ms": round(statistics.median(ordered) * 1000.0, 4),
        "p95_ms": round(_percentile(ordered, 0.95) * 1000.0, 4),
        "min_ms": round(ordered[0] * 1000.0, 4),
        "max_ms": round(ordered[-1] * 1000.0, 4),
    }


def machine_description() -> str:
    """One line naming the machine and interpreter the numbers were taken on."""
    cpu = platform.processor() or platform.machine()
    return f"{platform.system()} {platform.release()} {platform.machine()} ({cpu}), Python {platform.python_version()}"


def measure_shed_latency(iterations: int = DEFAULT_ITERATIONS, apps: int = DEFAULT_APPS) -> dict[str, Any]:
    """Runs the simulated shed path ``iterations`` times and returns latency statistics.

    Each iteration starts from NORMAL with ``apps`` deferrable (L3) applications tracked as
    RUNNING, then times one threshold-crossing sample through ``evaluate_mode`` and
    ``set_mode``. Raises ``RuntimeError`` if an iteration does not decide ``ECO_PRESERVATION``
    or does not leave every application PAUSED, so a broken shed path cannot report a time.

    Everything the run writes (registry file, mode file) lives in a temporary directory that
    is removed before returning. ``SOVEREIGN_MODE_FILE`` is pointed at it for the duration
    and restored afterwards; the manager's subscriptions on the global event bus are removed.
    """
    if iterations < 1:
        raise ValueError(f"iterations must be >= 1 (got {iterations})")
    if apps < 1:
        raise ValueError(f"apps must be >= 1 (got {apps})")

    bus = get_event_bus()
    paused_at: list[float] = []

    def on_enterprise_event(event: Event) -> None:
        if event.event_type.endswith(".paused"):
            paused_at.append(time.perf_counter())

    decision_s: list[float] = []
    paused_s: list[float] = []
    published_s: list[float] = []
    sample = {sentinel_copilot.SOC_METRIC: SHED_SOC_PERCENT, sentinel_copilot.SOLAR_METRIC: 0.0}

    saved_mode_file = os.environ.get("SOVEREIGN_MODE_FILE")
    saved_levels = {name: logging.getLogger(name).level for name in _QUIET_LOGGERS}
    manager: EnterpriseManager | None = None
    with tempfile.TemporaryDirectory(prefix="smdc-shed-bench-") as tmp_dir:
        try:
            for name in _QUIET_LOGGERS:
                logging.getLogger(name).setLevel(logging.CRITICAL)
            os.environ["SOVEREIGN_MODE_FILE"] = os.path.join(tmp_dir, "sovereign_mode")

            registry = EnterpriseRegistry(registry_file=Path(tmp_dir) / "registry.json")
            app_ids = [f"shed-bench-{index:02d}" for index in range(1, apps + 1)]
            for app_id in app_ids:
                manifest = EnterpriseRegistry.scaffold_manifest(
                    name=f"Shed benchmark {app_id}", app_id=app_id, power_tier=PowerPriority.L3_DEFERRABLE
                )
                ok, errors = registry.register_app(manifest, persist=False)
                if not ok:
                    raise RuntimeError(f"could not register benchmark app {app_id}: {'; '.join(errors)}")
            manager = EnterpriseManager(registry=registry, event_bus=bus)
            bus.subscribe("enterprise.*", on_enterprise_event)
            for app_id in app_ids:
                manager.start_app(app_id)

            def statuses() -> list[AppStatus]:
                assert manager is not None
                states = [manager.get_runtime_state(app_id) for app_id in app_ids]
                return [state.status for state in states if state is not None]

            for iteration in range(iterations):
                sentinel_copilot.set_mode(NOMINAL_MODE)  # resumes whatever the last iteration shed
                if statuses() != [AppStatus.RUNNING] * apps:
                    raise RuntimeError(f"iteration {iteration}: apps did not return to RUNNING")
                paused_at.clear()

                t0 = time.perf_counter()
                decision = sentinel_copilot.evaluate_mode(sample, 0.0)
                t_decided = time.perf_counter()
                if decision is None or decision[0] != SHED_MODE:
                    raise RuntimeError(f"iteration {iteration}: sentinel decided {decision!r}, expected {SHED_MODE}")
                sentinel_copilot.set_mode(decision[0])
                t_published = time.perf_counter()

                if statuses() != [AppStatus.PAUSED] * apps or len(paused_at) != apps:
                    raise RuntimeError(f"iteration {iteration}: not every deferrable app was paused")
                decision_s.append(t_decided - t0)
                paused_s.append(paused_at[-1] - t0)
                published_s.append(t_published - t0)
        finally:
            bus.unsubscribe("enterprise.*", on_enterprise_event)
            if manager is not None:
                bus.unsubscribe("load_shedding.*", manager._on_load_shedding_event)
                bus.unsubscribe("enterprise.*", manager._on_enterprise_telemetry)
            if saved_mode_file is None:
                os.environ.pop("SOVEREIGN_MODE_FILE", None)
            else:
                os.environ["SOVEREIGN_MODE_FILE"] = saved_mode_file
            for name, level in saved_levels.items():
                logging.getLogger(name).setLevel(level)

    return {
        "simulated": True,
        "hardware_actuation_measured": False,
        "machine": machine_description(),
        "iterations": iterations,
        "deferrable_apps": apps,
        "trigger": f"{sentinel_copilot.SOC_METRIC}={SHED_SOC_PERCENT} -> {SHED_MODE}",
        "stages": {
            "sample_to_mode_decision": summarize_ms(decision_s),
            "sample_to_apps_paused": summarize_ms(paused_s),
            "sample_to_mode_published": summarize_ms(published_s),
        },
    }
