"""Regression tests for tri-council review round 29.

1. ``sentinel_copilot.run_copilot()`` publishes when battery SoC reaches the ``100 %``
   or ``0 %`` endpoints even if the step from ``published_soc`` is smaller than
   ``SOC_REPUBLISH_DELTA_PERCENT``, so apps with high ``min_battery_soc`` recover at full
   charge instead of remaining held indefinitely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import sovereign_dc.agents.sentinel_copilot as sentinel_copilot
from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, get_event_bus


def _run_with_sequence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, socs: list[float]) -> list[dict[str, Any]]:
    monkeypatch.setenv("SOVEREIGN_MODE_FILE", str(tmp_path / "sovereign_mode"))
    payloads: list[dict[str, Any]] = []

    def record(event: Event) -> None:
        payloads.append(dict(event.payload))

    feed = iter(socs)

    def telemetry_feed() -> dict[str, float]:
        try:
            soc = next(feed)
        except StopIteration:
            raise KeyboardInterrupt from None
        return {sentinel_copilot.SOC_METRIC: soc}

    bus = get_event_bus()
    bus.subscribe("load_shedding.changed", record)
    try:
        with (
            patch.object(sentinel_copilot, "get_telemetry", side_effect=telemetry_feed),
            patch("time.sleep", return_value=None),
            pytest.raises(KeyboardInterrupt),
        ):
            sentinel_copilot.run_copilot()
    finally:
        bus.unsubscribe("load_shedding.changed", record)
    return payloads


def test_sentinel_publishes_transition_to_full_charge_and_resumes_min_battery_soc_99_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    manager = EnterpriseManager(registry=registry, event_bus=get_event_bus())
    manifest = EnterpriseRegistry.scaffold_manifest(name="High Floor App", app_id="app-99")
    manifest.power.tier = PowerPriority.L2_BACKGROUND
    manifest.power.min_battery_soc = 99.0
    registry.register_app(manifest)
    assert manager.start_app("app-99")[0]

    payloads = _run_with_sequence(monkeypatch, tmp_path, [98.0, 99.2, 100.0, 100.0])
    assert [p["battery_soc"] for p in payloads] == [98.0, 99.2, 100.0]

    state = manager.get_runtime_state("app-99")
    assert state is not None and state.status == AppStatus.RUNNING


def test_sentinel_publishes_transition_to_zero_soc_endpoint_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sentinel_copilot, "SHEDDING_THRESHOLDS", (50.0, 30.0, 20.0, 0.0))
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    manager = EnterpriseManager(registry=registry, event_bus=get_event_bus())
    manifest = EnterpriseRegistry.scaffold_manifest(name="Zero Floor App", app_id="app-0")
    manifest.power.tier = PowerPriority.L1_STANDARD
    manifest.power.min_battery_soc = 0.5
    registry.register_app(manifest)
    assert manager.start_app("app-0")[0]

    payloads = _run_with_sequence(monkeypatch, tmp_path, [0.6, 0.0, 0.0])
    assert [p["battery_soc"] for p in payloads] == [0.6, 0.0]

    state = manager.get_runtime_state("app-0")
    assert state is not None and state.status == AppStatus.PAUSED
