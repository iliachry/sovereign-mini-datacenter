"""Regression tests for tri-council review round 30.

1. ``hardware/ASSEMBLY_MANUAL.md`` Load-Shedder Verification checklist matches the
   exact ``EnterpriseManager`` tier shedding thresholds: level 3 (< 20%) pauses
   ``L2_BACKGROUND``, ``L3_DEFERRABLE``, and ``L4_IDLE``, while ``L1_STANDARD`` stays
   running until level 4 (< 10%) unless paused by ``min_battery_soc``.
"""

from __future__ import annotations

from pathlib import Path

from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, EventType, SovereignEventBus

ROOT = Path(__file__).resolve().parent.parent


def test_assembly_manual_load_shedder_checklist_matches_tier_thresholds(tmp_path: Path) -> None:
    manual = (ROOT / "hardware" / "ASSEMBLY_MANUAL.md").read_text(encoding="utf-8")
    assert "mark non-`L0_CRITICAL` tracked apps `paused`" not in manual
    for phrase in ("L2_BACKGROUND", "L3_DEFERRABLE", "L4_IDLE", "L1_STANDARD", "below 10%", "L0_CRITICAL"):
        assert phrase in manual

    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    manager = EnterpriseManager(registry=registry, event_bus=bus)
    for tier in PowerPriority:
        manifest = EnterpriseRegistry.scaffold_manifest(name=tier.value, app_id=tier.value.lower())
        manifest.power.tier = tier
        manifest.power.min_battery_soc = 0.0
        registry.register_app(manifest)
        assert manager.start_app(manifest.app_id)[0]

    bus.publish(
        Event(event_type=EventType.LOAD_SHEDDING_CHANGED, source="test", payload={"level": 3, "battery_soc": 19.0})
    )
    assert manager.get_runtime_state("l0_critical").status == AppStatus.RUNNING  # type: ignore[union-attr]
    assert manager.get_runtime_state("l1_standard").status == AppStatus.RUNNING  # type: ignore[union-attr]
    for tier in (PowerPriority.L2_BACKGROUND, PowerPriority.L3_DEFERRABLE, PowerPriority.L4_IDLE):
        st = manager.get_runtime_state(tier.value.lower())
        assert st is not None and st.status == AppStatus.PAUSED

    bus.publish(
        Event(event_type=EventType.LOAD_SHEDDING_CHANGED, source="test", payload={"level": 4, "battery_soc": 9.0})
    )
    assert manager.get_runtime_state("l0_critical").status == AppStatus.RUNNING  # type: ignore[union-attr]
    assert manager.get_runtime_state("l1_standard").status == AppStatus.PAUSED  # type: ignore[union-attr]
