"""Regression tests for tri-council review round 23.

1. ``EnterpriseManager.start_app()`` checks shedding plan staleness and re-evaluates
   current causes under ``self._lock`` before updating ``_shed_paused``, so a
   concurrent newer shedding event's ``{'soc_floor'}`` cause is never overwritten by
   an older ``{'tier'}`` plan.
2. Windows (``fcntl=None``) ``exclusive_file_lock`` propagates persistent read/permission
   errors immediately instead of spinning past ``timeout_seconds``, and enforces
   ``deadline`` even under repeated disappearance races.
3. ``README.md``, ``AGENTS.md``, ``COMMERCIALIZATION.md``, and ``ARCHITECTURE.md`` §8
   reflect actual test coverage (``92.1%``) and target-design cold-start status.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import sovereign_dc.enterprise.registry as registry_mod
from sovereign_dc.enterprise.manager import AppStatus, EnterpriseManager
from sovereign_dc.enterprise.registry import EnterpriseRegistry, exclusive_file_lock
from sovereign_dc.enterprise.schema import PowerPriority
from sovereign_dc.events import Event, SovereignEventBus

ROOT = Path(__file__).resolve().parent.parent


def test_start_app_does_not_overwrite_newer_shedding_causes_during_pause_recheck(tmp_path: Path) -> None:
    bus = SovereignEventBus()
    registry = EnterpriseRegistry(registry_file=tmp_path / "registry.json")
    manifest = EnterpriseRegistry.scaffold_manifest("Worker", "worker", power_tier=PowerPriority.L4_IDLE)
    manifest.power.min_battery_soc = 20.0
    assert registry.register_app(manifest)[0]
    manager = EnterpriseManager(registry=registry, event_bus=bus)
    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 1, "battery_soc": 90.0}))

    injected: list[bool] = []
    orig_resume = manager.resume_app
    orig_shed_causes = manager._shed_causes

    # Simulate a concurrent shedding event landing right before the PAUSED cause-sync
    # lock re-acquisition in start_app(), switching level=0 / SoC=5% ({'soc_floor'}).
    def intercept_shed_causes(manifest, level, soc, previous):  # type: ignore[no-untyped-def]
        causes = orig_shed_causes(manifest, level, soc, previous)
        if not injected and causes == {"tier"}:
            # First call during start_app post-.started check happens while PAUSED under tier;
            # inject concurrent level 0 / SoC 5% before start_app writes _shed_paused.
            rt = manager.get_runtime_state("worker")
            if rt is not None and rt.status == AppStatus.PAUSED:
                injected.append(True)
                bus.publish(
                    Event(event_type="load_shedding.changed", source="test", payload={"level": 0, "battery_soc": 5.0})
                )
        return causes

    manager._shed_causes = intercept_shed_causes  # type: ignore[method-assign]
    manager.resume_app = orig_resume  # type: ignore[method-assign]

    ok, _ = manager.start_app("worker")
    assert ok is True
    assert manager._shed_paused.get("worker") == {"soc_floor"}

    # A subsequent level-0 event without SoC must keep the app PAUSED (still under soc_floor).
    bus.publish(Event(event_type="load_shedding.changed", source="test", payload={"level": 0}))
    rt_after = manager.get_runtime_state("worker")
    assert rt_after is not None and rt_after.status == AppStatus.PAUSED
    assert manager._shed_paused.get("worker") == {"soc_floor"}


def test_windows_fallback_lock_fails_fast_on_unreadable_lock_and_bounds_enoent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "unreadable.lock"
    excl_path = tmp_path / "unreadable.lock.excl"
    excl_path.write_text("99999999:1:abc\n", encoding="utf-8")

    orig_read_bytes = Path.read_bytes

    def fail_read(self: Path) -> bytes:
        if self == excl_path:
            raise PermissionError("unreadable lock file")
        return orig_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(PermissionError, match="unreadable lock file"):
        with exclusive_file_lock(lock_path, use_fcntl=None, timeout_seconds=0.02, poll_seconds=0.005):
            pass

    # Repeated disappearance race (FileNotFoundError) must still terminate by deadline.
    def gone_read(self: Path) -> bytes:
        if self == excl_path:
            raise FileNotFoundError("vanished between calls")
        return orig_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", gone_read)
    monkeypatch.setattr(registry_mod.os, "link", lambda _src, _dst: (_ for _ in ()).throw(FileExistsError()))
    with pytest.raises(TimeoutError, match="held by another process"):
        with exclusive_file_lock(lock_path, use_fcntl=None, timeout_seconds=0.02, poll_seconds=0.002):
            pass


def test_docs_coverage_and_architecture_cold_start_status() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    comm = (ROOT / "COMMERCIALIZATION.md").read_text(encoding="utf-8")
    assert "92.1%25%20Cov" in readme
    assert "92.1% coverage" in readme
    assert "92.1% coverage" in agents
    assert "92.1% Coverage" in comm

    arch = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "## 8. Disaster Recovery, Black-Start & Self-Healing Protocol (Target Design)" in arch
    assert "> **Target design, not implemented.** Autonomous relay switching" in arch
