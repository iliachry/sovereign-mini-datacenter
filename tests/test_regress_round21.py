"""Regression tests for tri-council review round 21.

1. ``COMMERCIALIZATION.md`` Year-1 margin cells (``29.7%``, ``-18.5%``) and sensitivity
   figures (``22.7%`` / ``36.7%``) match the stated derivation formula.
2. ``EnterpriseManager.start_app()`` respects an operator ``resume_app()`` called inside
   a ``.paused`` subscriber without re-pausing on the same shedding generation, and
   pauses again only when a new shedding event arrives.
3. Firmware 64-bit monotonic frame-age accumulators and ``hardware/ASSEMBLY_MANUAL.md`` /
   ``hardware/WIRING_DIAGRAM.md`` D5 coolant pump branch fuse commissioning order.
"""

from __future__ import annotations

from pathlib import Path

from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, SovereignEventBus

ROOT = Path(__file__).resolve().parent.parent


def test_commercialization_year1_margins_match_stated_derivation() -> None:
    comm = (ROOT / "COMMERCIALIZATION.md").read_text(encoding="utf-8")
    assert "Gross Margin (%)        29.7%        30.1%        30.0%        30.1%        30.6%" in comm
    assert "EBITDA Margin (%)      -18.5%        -3.3%         1.7%         2.8%         4.7%" in comm
    assert "with services at 0% or 100% margin, Year 1 gross margin would be 22.7% or 36.7%." in comm

    # Verify Year 1 arithmetic directly against the stated formula:
    hw_rev = 45 * 0.0315
    arr_rev = 0.11
    srv_rev = 0.25
    tot_rev = hw_rev + arr_rev + srv_rev
    gp = 45 * 0.006799 + arr_rev * 0.88 + srv_rev * 0.50
    assert round(gp / tot_rev * 100, 1) == 29.7
    assert round((-0.33 / 1.78) * 100, 1) == -18.5
    assert round((45 * 0.006799 + arr_rev * 0.88) / tot_rev * 100, 1) == 22.7
    assert round((45 * 0.006799 + arr_rev * 0.88 + srv_rev) / tot_rev * 100, 1) == 36.7


def test_start_app_respects_operator_resume_inside_paused_callback(tmp_path: Path) -> None:
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    assert registry.register_app(
        EnterpriseRegistry.scaffold_manifest("Worker", "worker", power_tier=PowerPriority.L4_IDLE)
    )[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)
    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 5.0}))

    events_seen: list[str] = []
    bus.subscribe("enterprise.worker.started", lambda _e: events_seen.append("started"))
    bus.subscribe("enterprise.worker.resumed", lambda _e: events_seen.append("resumed"))

    overridden: list[bool] = []

    def on_paused(_event: Event) -> None:
        events_seen.append("paused")
        if not overridden:
            overridden.append(True)
            ok_r, _ = manager.resume_app("worker", "operator override")
            assert ok_r is True

    bus.subscribe("enterprise.worker.paused", on_paused)
    ok, msg = manager.start_app("worker")
    assert ok is True
    assert "held paused by load shedding" not in msg
    assert events_seen == ["started", "paused", "resumed"]

    state = manager.get_runtime_state("worker")
    assert state is not None and state.status == AppStatus.RUNNING
    assert "worker" not in manager._shed_paused

    # Next load-shedding event overrides the earlier operator resume as documented.
    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 4, "battery_soc": 4.0}))
    state_after = manager.get_runtime_state("worker")
    assert state_after is not None and state_after.status == AppStatus.PAUSED
    assert "worker" in manager._shed_paused


def test_firmware_64bit_frame_age_accumulator_and_assembly_pump_commissioning_order() -> None:
    ino = (ROOT / "firmware" / "esp32_telemetry_bridge.ino").read_text(encoding="utf-8")
    assert "uint64_t currentVeFrameAgeMs()" in ino
    assert "veAgeAccumMs += (uint32_t)(now32 - veAgeAnchorMs);" in ino

    esp = (ROOT / "firmware" / "esphome_smdc_bridge.yaml").read_text(encoding="utf-8")
    assert "id: ve_age_accum_ms" in esp
    assert "id(ve_age_accum_ms) += static_cast<uint32_t>(now - id(ve_age_anchor_ms));" in esp

    asm = (ROOT / "hardware" / "ASSEMBLY_MANUAL.md").read_text(encoding="utf-8")
    assert "Leave the D5 coolant pump's branch fuse out of the 4-way 12V fuse block" in asm
    assert "D5 coolant pump branch fuse in the 4-way 12V fuse block is removed" in asm
    assert "Fill reservoir with EK CryoFuel Clear, then insert the D5 pump's branch fuse" in asm

    wd = (ROOT / "hardware" / "WIRING_DIAGRAM.md").read_text(encoding="utf-8")
    assert "leave the D5 pump branch fuse out of the 4-way 12V fuse block until the cooling loop" in wd
